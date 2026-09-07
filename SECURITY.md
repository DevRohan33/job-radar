# Security

## Reporting a vulnerability

Email **parveagr@gmail.com** with `[job-radar security]` in the subject. Please
do not open a public issue for anything that exposes credentials or user data.
Expect a first reply within a week.

## What this project touches

Job Radar handles four credentials. None of them are ever written to the
repository, the logs, or the Google Sheet.

| Credential | Scope | Where it lives |
|---|---|---|
| Gmail App Password | IMAP read-only (`BODY.PEEK`, `readonly=True`) | `.env` locally, repo secret in CI |
| Google service account key | One spreadsheet, `spreadsheets` scope | A file outside the repo locally, repo secret in CI |
| OpenAI API key | Optional, scoring only | `.env` / repo secret |
| Telegram bot token | Optional, digest only | `.env` / repo secret |

Design decisions that follow from that:

- **App Password over OAuth.** No consent screen, no refresh token to leak or
  expire, and the password is revocable from your Google account page alone.
- **Read-only mail access.** The IMAP session is opened `readonly=True` and
  fetches with `BODY.PEEK`, so a bug cannot delete or even mark your mail read.
- **Least-privilege Sheets access.** The service account can reach exactly the
  one spreadsheet you shared with it, and nothing else in your Drive.
- **Your CV stays local.** `profile/cv.md` is gitignored; only
  `profile/cv.example.md` is committed.
- **No credential ever reaches the model.** Only the job description, your
  skills tiers and your CV text are sent to the scoring API.

## If you fork this

Rotate nothing of the author's — there is nothing to rotate; this repository has
never contained a live credential. Add your own via `.env` (gitignored) or
repository secrets, and run `python -m job_radar check` to confirm they work
before enabling the schedule.
