"""Command line entry point.

    python -m job_radar run --dry-run --fixtures tests/fixtures
    python -m job_radar check
    python -m job_radar save-fixtures --days 7
    python -m job_radar cards --fixtures tests/fixtures
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import PROFILE_DIR, ROOT, get_settings
from .profile import load_cv, load_filters, load_skills
from .pipeline import RunOptions, run
from .sources.gmail_linkedin import fetch_emails, load_fixtures, save_fixtures
from .sources.parse import parse_email


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.lookback:
        settings.lookback_days = args.lookback
    if args.no_enrich:
        settings.enrich_enabled = False
    options = RunOptions(
        fixtures=args.fixtures,
        dry_run=args.dry_run,
        csv_only=args.csv,
        limit=args.limit,
        skip_enrich=args.no_enrich,
        skip_llm=args.no_llm,
    )
    report = run(settings, options)
    print("\n" + report.summary())

    if report.zero_yield:
        print(
            "\nZERO YIELD: emails arrived but no job cards parsed. "
            "LinkedIn most likely changed the alert HTML - save a fresh fixture "
            "(python -m job_radar save-fixtures) and fix the parser.",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_cards(args: argparse.Namespace) -> int:
    """Print what the parser sees. The first thing to run when yield looks wrong."""
    settings = get_settings()
    emails = load_fixtures(args.fixtures) if args.fixtures else fetch_emails(settings, args.days)
    total = 0
    for email in emails:
        cards = parse_email(email)
        total += len(cards)
        print(f"\n=== {email.received} | {email.subject[:80]!r} -> {len(cards)} card(s)")
        for card in cards:
            print(
                f"  [{card.job_id}] {card.role_title}\n"
                f"      company={card.company!r} location={card.location!r}\n"
                f"      posted={card.posted_hint!r} applicants={card.applicants_hint} "
                f"salary={card.salary_hint!r}\n"
                f"      {card.apply_url}"
            )
    print(f"\n{total} card(s) from {len(emails)} email(s)")
    return 0 if total or not emails else 2


def cmd_save_fixtures(args: argparse.Namespace) -> int:
    """Save real alert emails as .eml - the parser's regression safety net."""
    saved = save_fixtures(get_settings(), Path(args.out), args.days, args.max)
    for path in saved:
        print(f"saved {path}")
    print(f"\n{len(saved)} fixture(s) in {args.out}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Verify every credential and profile file before trusting the cron."""
    settings = get_settings()
    ok = True

    print("profile/")
    try:
        skills = load_skills()
        tiers = ", ".join(f"{t.name}:{len(t.skills)}" for t in skills.tiers.values())
        print(f"  OK   skills.yaml ({tiers}; {len(skills.synonyms)} synonym groups)")
    except Exception as exc:
        ok = False
        print(f"  FAIL skills.yaml: {exc}")
    try:
        filters = load_filters()
        total = sum(filters.weights.values())
        print(
            f"  OK   filters.yaml ({len(filters.title_blocklist)} blocked title terms, "
            f"weights sum to {total:g})"
        )
        if abs(total - 100) > 0.01:
            print(f"       WARNING: weights sum to {total:g}, not 100 - scores will not reach 100")
    except Exception as exc:
        ok = False
        print(f"  FAIL filters.yaml: {exc}")
    cv = load_cv()
    if "Replace everything below" in cv:
        ok = False
        print(f"  FAIL cv.md is still the template - paste your real CV into {PROFILE_DIR / 'cv.md'}")
    elif cv.strip():
        print(f"  OK   cv.md ({len(cv.split())} words)")
    else:
        ok = False
        print(f"  FAIL cv.md is empty or missing at {PROFILE_DIR / 'cv.md'}")

    print("\nGmail IMAP")
    try:
        emails = fetch_emails(settings, since_days=args.days)
        print(f"  OK   logged in, {len(emails)} LinkedIn email(s) in the last {args.days} day(s)")
        if not emails:
            print("       (no alert emails yet - create LinkedIn saved searches with daily alerts)")
    except Exception as exc:
        ok = False
        print(f"  FAIL {exc}")

    print("\nGoogle Sheets")
    try:
        from .sinks.sheets import existing_job_ids, open_worksheet

        worksheet = open_worksheet(settings)
        print(
            f"  OK   opened {worksheet.spreadsheet.title!r} / tab {worksheet.title!r}, "
            f"{len(existing_job_ids(worksheet))} job id(s) already tracked"
        )
    except Exception as exc:
        ok = False
        print(f"  FAIL {exc}")

    print("\nAI scoring (optional)")
    if not settings.llm_enabled:
        print("  SKIP LLM_ENABLED is off - the free rule scorer is in use")
    elif not settings.openai_api_key:
        ok = False
        print("  FAIL LLM_ENABLED is on but OPENAI_API_KEY is empty")
    else:
        try:
            import openai

            client = openai.OpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.llm_base_url or None,
                timeout=30.0,
            )
            client.chat.completions.create(
                model=settings.llm_model,
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with OK."}],
            )
            print(f"  OK   {settings.llm_model} reachable")
        except Exception as exc:
            ok = False
            print(f"  FAIL {exc}")

    print("\nnotifications (optional)")
    if settings.telegram_bot_token and settings.telegram_chat_id:
        print("  OK   Telegram digest configured")
    else:
        print("  SKIP no Telegram digest configured")

    print("\n" + ("all required checks passed" if ok else "some checks FAILED - see SETUP.md"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job_radar", description="Daily LinkedIn job pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="the full daily pipeline")
    run_cmd.add_argument("--fixtures", help="parse .eml files from this directory instead of Gmail")
    run_cmd.add_argument("--dry-run", action="store_true", help="score but write nothing")
    run_cmd.add_argument("--csv", action="store_true", help="write state/jobs.csv instead of Sheets")
    run_cmd.add_argument("--limit", type=int, help="cap how many jobs are scored")
    run_cmd.add_argument("--no-enrich", action="store_true", help="skip fetching job descriptions")
    run_cmd.add_argument("--no-llm", action="store_true", help="skip AI scoring")
    run_cmd.add_argument("--lookback", type=int, help="days of email history to read")
    run_cmd.set_defaults(func=cmd_run)

    cards_cmd = sub.add_parser("cards", help="show what the email parser extracts")
    cards_cmd.add_argument("--fixtures", help="parse .eml files from this directory")
    cards_cmd.add_argument("--days", type=int, default=2)
    cards_cmd.set_defaults(func=cmd_cards)

    fx = sub.add_parser("save-fixtures", help="save real alert emails as .eml test fixtures")
    fx.add_argument("--days", type=int, default=7)
    fx.add_argument("--max", type=int, default=10)
    fx.add_argument("--out", default=str(ROOT / "tests" / "fixtures"))
    fx.set_defaults(func=cmd_save_fixtures)

    check = sub.add_parser("check", help="verify credentials and profile files")
    check.add_argument("--days", type=int, default=3)
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger("job_radar").exception("run failed: %r", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
