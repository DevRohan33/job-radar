"""Stage 5 - Publish: dedup against the sheet, then append.

The sheet is the pipeline's memory. Reading back the job_id column is what
prevents duplicates - no state file to commit, no database to run. Your `status`
and `notes` columns are written once (blank) and never touched again.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import gspread
from google.oauth2.service_account import Credentials

from ..config import Settings
from ..models import SHEET_COLUMNS, Job, job_to_row

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
STATUS_OPTIONS = ["New", "Shortlist", "Applied", "Interview", "Closed"]


def _credentials(settings: Settings) -> Credentials:
    if settings.google_service_account_file:
        return Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
    raw = settings.google_service_account_json
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole "
            "service-account key file contents into the secret, newlines and all."
        ) from exc
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def open_worksheet(settings: Settings) -> gspread.Worksheet:
    """Open (or create) the Jobs tab, with its header row guaranteed."""
    settings.require_sheets()
    client = gspread.authorize(_credentials(settings))
    try:
        spreadsheet = client.open_by_key(settings.sheet_id)
    except gspread.SpreadsheetNotFound as exc:
        raise RuntimeError(
            f"Sheet {settings.sheet_id} not found. Share it with the service "
            "account's client_email as an Editor."
        ) from exc

    try:
        worksheet = spreadsheet.worksheet(settings.sheet_tab)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=settings.sheet_tab, rows=1000, cols=len(SHEET_COLUMNS)
        )

    ensure_header(worksheet)
    return worksheet


def ensure_header(worksheet: gspread.Worksheet) -> None:
    header = worksheet.row_values(1)
    if header == SHEET_COLUMNS:
        return
    if header and header != SHEET_COLUMNS:
        log.warning("sheets: header differs from SHEET_COLUMNS - rewriting row 1")
    if worksheet.col_count < len(SHEET_COLUMNS):
        worksheet.add_cols(len(SHEET_COLUMNS) - worksheet.col_count)
    worksheet.update(values=[SHEET_COLUMNS], range_name="A1")
    _format_header(worksheet)


def _format_header(worksheet: gspread.Worksheet) -> None:
    """Freeze the header and the first four columns, add the status dropdown."""
    sheet_id = worksheet.id
    status_index = SHEET_COLUMNS.index("status")
    requests: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 4},
                },
                "fields": "gridProperties(frozenRowCount,frozenColumnCount)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "startColumnIndex": status_index,
                    "endColumnIndex": status_index + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": v} for v in STATUS_OPTIONS],
                    },
                    "showCustomUi": True,
                    "strict": False,
                },
            }
        },
    ]
    try:
        worksheet.spreadsheet.batch_update({"requests": requests})
    except Exception as exc:  # cosmetic only - never fail a run over formatting
        log.warning("sheets: could not apply formatting: %r", exc)


def existing_job_ids(worksheet: gspread.Worksheet) -> set[str]:
    column = SHEET_COLUMNS.index("job_id") + 1
    values = worksheet.col_values(column)[1:]  # drop header
    return {v.strip() for v in values if v.strip()}


def append_jobs(worksheet: gspread.Worksheet, jobs: Iterable[Job]) -> list[Job]:
    """Append only jobs whose id is not already in the sheet. Returns what was written."""
    known = existing_job_ids(worksheet)
    fresh = [j for j in jobs if j.job_id not in known]
    # Highest score first, so today's block reads top-down.
    fresh.sort(key=lambda j: j.match_score, reverse=True)
    if not fresh:
        log.info("sheets: nothing new to append (%d ids already present)", len(known))
        return []
    worksheet.append_rows(
        [job_to_row(j) for j in fresh],
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
        table_range="A1",
    )
    log.info("sheets: appended %d new job(s)", len(fresh))
    return fresh
