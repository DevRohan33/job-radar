# Setup

Everything here happens in your browser and takes about 40 minutes once. The
code is already written; this is the part only you can do.

At the end, run `python -m job_radar check` — it verifies every credential
below and tells you exactly which one is wrong.

---

## 1. LinkedIn saved searches (10 min)

1. Search for a role on LinkedIn Jobs, apply your filters (location, date
   posted, experience level).
2. Click **Create job alert** (or the bell on the saved search).
3. Set frequency to **Daily**, delivery to **Email**.
4. Repeat for **3–5 searches**, split by role and location rather than one broad
   search — narrow alerts give better recall, and the `source` column in the
   sheet will later tell you which searches actually earn their keep.

Good split for a Python/data search:

| Alert | Keywords | Location |
|---|---|---|
| 1 | Python Developer | India (Remote) |
| 2 | Data Engineer | Bengaluru |
| 3 | Backend Engineer | Hyderabad / Pune |
| 4 | Data Analyst | India |

Alerts start arriving the next morning. The pipeline can run before then; it
will simply find nothing.

---

## 2. Gmail App Password (5 min)

**Not** your Google account password, and not OAuth — an OAuth consent screen
left in Testing status expires its refresh token after 7 days and the automation
dies silently (Trap 1 in the design doc).

1. Turn on **2-Step Verification**: <https://myaccount.google.com/signinoptions/two-step-verification>
2. Go to <https://myaccount.google.com/apppasswords>
3. Name it `job-radar`, click **Create**.
4. Copy the 16-character password. Spaces are stripped automatically, so paste
   it however Google shows it.

IMAP must also be on: Gmail → Settings → **See all settings** → **Forwarding and
POP/IMAP** → *Enable IMAP* → Save.

---

## 3. Google Sheet + service account (15 min)

**Create the sheet**

1. New blank sheet at <https://sheets.new>. Name it `Job Radar`.
2. Copy the id out of the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_STRING`**`/edit`

You do not need to create any tabs or headers — the script creates the `Jobs`
tab, writes the header row, freezes it, and adds the `status` dropdown.

**Create the service account**

1. <https://console.cloud.google.com/> → create a project called `job-radar`.
2. **APIs & Services → Library** → search **Google Sheets API** → **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   Name it `job-radar-writer`, skip the optional role/user steps, **Done**.
4. Click the new service account → **Keys** → **Add key → Create new key →
   JSON**. A `.json` file downloads. This is a secret; keep it out of git.
5. Open that file and copy the `client_email` value — it looks like
   `job-radar-writer@job-radar-123456.iam.gserviceaccount.com`.
6. Back in your sheet: **Share** → paste that address → give it **Editor** →
   untick "Notify people" → **Share**.

Sharing the sheet with the service account is the step people forget. Without
it you get `SpreadsheetNotFound` even though the id is correct.

---

## 4. Local configuration

```bash
cp .env.example .env
```

Fill in `.env`:

```ini
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
SHEET_ID=1AbC...long-id...XyZ
GOOGLE_SERVICE_ACCOUNT_FILE=C:/path/to/job-radar-123456.json
```

`GOOGLE_SERVICE_ACCOUNT_FILE` points at the downloaded key file and is the easy
option locally. In GitHub Actions you use `GOOGLE_SERVICE_ACCOUNT_JSON` instead,
with the file's whole contents pasted in as a secret.

Then install and verify:

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m job_radar check
```

Every line should read `OK`. Then:

```bash
python -m job_radar run --dry-run     # scores, writes nothing
python -m job_radar run               # writes to your sheet
```

---

## 5. Your profile

Three files under `profile/` decide everything about ranking:

- **`cv.md`** — paste your resume as plain text. Keep the real wording; the
  phrases a job description echoes are what the similarity score picks up.
- **`skills.yaml`** — four tiers (`core`, `strong`, `familiar`, `learnable`),
  each with a `weight`, plus `tracked_gaps` and the `synonyms` map. Synonyms
  matter most: they are what makes "Retrieval Augmented Generation", "RAG" and
  "retrieval-augmented" count as one skill.
- **`filters.yaml`** — the hard gates (`title_must_not_contain`, `locations`,
  `experience`, `compensation`), **and** the rubric: `weights` (must sum to
  100), `verdict_bands`, and the `enrichment` budget.

`python -m job_radar check` parses both and warns if the weights do not sum to
100.

---

## 6. GitHub Actions

Push the repo, then add the secrets under
**Settings → Secrets and variables → Actions**.

**Secrets** (tab: *Secrets*)

| Name | Value |
|---|---|
| `GMAIL_USER` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password |
| `SHEET_ID` | the long id from the sheet URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the **entire contents** of the downloaded JSON key file |
| `CV_MARKDOWN` | the **entire contents** of your `profile/cv.md` |
| `OPENAI_API_KEY` | only if you turn on AI scoring |
| `TELEGRAM_BOT_TOKEN` | only if you want the morning digest |
| `TELEGRAM_CHAT_ID` | only if you want the morning digest |

`CV_MARKDOWN` is the one that is easy to forget. `profile/cv.md` is gitignored,
so it is **not** in the pushed repository — without this secret the runner has no
CV at all, and the run still exits `0`. It just scores worse: the CV↔JD
similarity term drops to zero and the AI layer compares every job against an
empty CV. Paste the whole file, formatting and all.

**Variables** (tab: *Variables* — these are not secret)

| Name | Suggested |
|---|---|
| `LLM_ENABLED` | `false` to start, `true` once you want AI scoring |
| `LLM_MODEL` | `gpt-4o-mini` |
| `ENRICH_TOP_N` | `12` |

Then **Actions → Job Radar - daily run → Run workflow** to test it by hand
before trusting the 07:15 schedule. Tick *dry run* for the first one.

The workflow needs write permission for its heartbeat commit: **Settings →
Actions → General → Workflow permissions → Read and write permissions**.

---

## 7. Optional: AI scoring (OpenAI)

The free rule-based scorer is on by default and needs no key. The AI layer only
sees the top ~15 jobs a day.

Get a key at <https://platform.openai.com/api-keys> (a paid balance is required;
the free tier does not cover the API). Then:

```ini
LLM_ENABLED=true
OPENAI_API_KEY=sk-proj-...
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=
```

`gpt-4o-mini` is the cheap default and handles this task well. Set `LLM_MODEL`
to any model your account can reach. The scorer asks for strict JSON schema
output and automatically falls back to plain JSON mode if a model rejects it, so
older models and OpenAI-compatible proxies work too — point `LLM_BASE_URL` at
Groq or Together to use those instead, with no code change.

Verify with `python -m job_radar check`, which makes one tiny real call.

If the key is missing, rate-limited, or the API errors, the run silently keeps
the rule scores. The AI layer is an upgrade, never a dependency.

---

## 8. Optional: morning digest

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → copy
   the token.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

You get the day's top five with scores and reasons, above `DIGEST_MIN_SCORE`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `AUTHENTICATIONFAILED` on IMAP | Using your Google password, not an App Password; or 2-Step Verification is off |
| IMAP login works, 0 emails | No alerts arrived yet, or they are in Promotions — that is still INBOX, so check `--lookback 7` first |
| `SpreadsheetNotFound` | The sheet is not shared with the service account's `client_email` |
| `GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON` | Only part of the key file was pasted |
| Cards parse but company is wrong | LinkedIn changed the email layout — run `python -m job_radar cards --fixtures tests/fixtures` |
| `HTTP 999` in the log | LinkedIn refused enrichment from a datacenter IP. Expected; the run degrades to email-only metadata |
| Workflow stopped running after ~2 months | The heartbeat commit failed — check workflow write permissions |
