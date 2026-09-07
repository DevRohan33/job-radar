<div align="center">

# Job Radar

**A daily job pipeline that reads LinkedIn's own alert emails, scores every opening against your CV, and appends the ranked result to one Google Sheet.**

No scraping. No browser automation. No frontend. One cron job and a spreadsheet.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-57%20offline-brightgreen.svg)](tests/)
[![Runs on](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/daily.yml)
[![Cost](https://img.shields.io/badge/cost-%E2%82%B90%20%2F%20month-informational.svg)](#what-it-costs)

</div>

---

## The problem

You want the ten jobs worth your morning, not the two hundred a job board thinks
you should see. Every tool that promises this either scrapes LinkedIn — which
breaks their terms and risks the one account your job search depends on — or
ranks by keyword count, which puts a job asking for "Python" five times above the
one that actually fits you.

Job Radar takes the boring, legitimate path: **LinkedIn will email you matching
jobs every day, for free, if you ask it to.** That email is a permission-granted
feed sitting in your inbox. This pipeline reads it, enriches the shortlist,
scores each role against your own skill tiers and CV, and writes one row per job
into a sheet you own.

```
   Gmail IMAP  ─▶  parse alert HTML  ─▶  gate + enrich  ─▶  score  ─▶  Google Sheet
    stage 1           stage 2              stage 3        stage 4      stage 5
   App Password    selectolax +         hard filters,    tiered       dedup on
   BODY.PEEK       text fallback        top-N JD fetch   rubric       job_id
   read-only       never raises         cached forever   + optional   never touches
                                                          OpenAI      your columns
```

Each stage hands a normalized `Job` record to the next, so any one of them can be
replaced without touching the others.

---

## Table of contents

- [Quick start](#quick-start) · [Commands](#commands) · [How scoring works](#how-scoring-works)
- [Configuration](#configuration) · [Deployment](#deployment) · [What lands in the sheet](#what-lands-in-the-sheet)
- [What it costs](#what-it-costs) · [Design notes](#design-notes) · [Testing](#testing)
- [Project layout](#project-layout) · [Roadmap](#roadmap) · [Author](#author) · [License](#license)

---

## Quick start

Everything below runs offline against committed fixtures — no credentials, no
network, no API spend.

```bash
git clone https://github.com/DevRohan33/job-radar.git
cd job-radar

python -m venv .venv
.venv\Scripts\activate                    # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt

cp profile/cv.example.md profile/cv.md    # then paste your real CV into it

pytest -q                                           # 57 tests
python -m job_radar cards --fixtures tests/fixtures  # what the parser extracts
python -m job_radar run --fixtures tests/fixtures --dry-run
```

When that looks right, add your credentials and go live — the full walkthrough
is in **[SETUP.md](SETUP.md)** (about 40 minutes, all of it in your browser):

```bash
python -m job_radar check     # verifies every credential, names whichever is wrong
python -m job_radar run       # writes to your sheet
```

---

## Commands

| Command | What it does |
|---|---|
| `run` | The full pipeline. `--dry-run` `--csv` `--limit N` `--no-enrich` `--no-llm` `--lookback N` `--fixtures DIR` |
| `check` | Parses both YAML files (warns if weights ≠ 100), logs into IMAP, opens the sheet, pings the model |
| `cards` | Prints exactly what the email parser sees. The first thing to run when yield looks wrong |
| `save-fixtures` | Saves real alert emails as `.eml` — the parser's regression net |

Exit codes are meaningful, because a cron job that fails silently is worse than
one that fails loudly:

| Code | Meaning |
|---|---|
| `0` | Ran fine |
| `1` | Crashed — the workflow opens a GitHub issue |
| `2` | **Zero yield**: emails arrived, nothing parsed. LinkedIn changed the HTML |

---

## How scoring works

### Gate first, then weigh

A job is **rejected outright** — never scored, never enriched, never sent to a
model — if it fails any gate in `filters.yaml`:

| Gate | Rejects |
|---|---|
| `title_must_not_contain` | Whole-word match, so `lead` blocks "Lead Engineer" but not "Leadership Team" |
| `company_blocklist` | Case-insensitive substring |
| `experience.reject_at_or_above` | Years demanded in the title or JD |
| `experience.accept_internships` / `accept_contract` | Employment type |
| `locations.accept_onsite` | On-site or hybrid outside your cities |
| `locations.accept_remote_scopes` | Remote roles scoped to another country |
| `compensation.reject_unpaid` | "unpaid", "equity only", "stipend only" |
| `compensation.hard_floor_lpa` | Advertised comp below your floor |

### The rubric

Whatever survives is scored 0–100 on weights read from your own `filters.yaml`:

| Weight | Component | What it measures |
|---:|---|---|
| 33 | `must_have_skills` | Core-tier overlap, plus how much of the JD's stack you own — tier-weighted, so a `core` hit counts 4× a `learnable` one |
| 15 | `seniority_fit` | Years required vs your ceiling, and the inferred band |
| 13 | `adjacent_skills` | Familiar + learnable hits, plus TF-IDF similarity between your CV and the JD |
| 13 | `freshness` | Age of the posting × applicant count |
| 12 | `ai_domain_bonus` | Primary target-role title match, plus AI-signal skills in the JD |
| 9 | `location_mode` | Position in your `work_mode_preference` order |
| 5 | `compensation` | Advertised comp against `reference_lpa` |

→ **85+** Apply now · **70–84** Strong · **55–69** Worth a look · **<55** Archive
(thresholds from `verdict_bands`).

Three deliberate choices behind those numbers:

- **Freshness is weighted like a skill.** A 90-point match posted four days ago
  with 200 applicants is a worse use of today's hour than a 70-point match posted
  this morning. The score answers *where do I spend the next hour*, not *what is
  the best job in the abstract*.
- **A job scored from the email alone is capped.** No JD fetched means the skill
  component is scaled to 70% — title-only evidence must not manufacture a top
  score.
- **Every component is recorded.** `job.score_breakdown` holds all seven, and the
  sheet's `why_matched` column explains the number in one sentence. A score you
  cannot audit is a score you will stop trusting by week three.

### Rule-based, then optionally AI

The **rule scorer is free** and needs no key: tier-weighted skill matching
through your synonym map, plus TF-IDF similarity between your CV and the JD. It
catches most of what matters.

The **optional OpenAI layer** rescores only the day's top ~15 with strict JSON
schema output. That is what catches *"they want distributed systems experience
and your CV describes it without ever using the phrase"* — judgement keyword
matching cannot make. It falls back to the rule score on any failure: no key,
rate limit, bad request, non-JSON reply. The AI layer is an upgrade, never a
dependency.

---

## Configuration

Two YAML files under `profile/` decide everything. **No thresholds, weights or
job titles are hardcoded in Python** — `src/job_radar/profile.py` is the only
module that reads them.

### `profile/skills.yaml`

Four tiers, each with a weight that multiplies every hit in it:

```yaml
core:                 # production-evidenced. A JD asking for these is a strong match
  weight: 1.0
  skills: [python, fastapi, rag, langchain, postgresql, docker]

strong:               # real shipped usage, less depth
  weight: 0.75
  skills: [multi_agent, vector_db, react, pandas, aws]

familiar:             # working knowledge; wouldn't lead with it in an interview
  weight: 0.4
  skills: [kubernetes, ci_cd, sql, linux]

learnable:            # not yours yet, but close enough not to tank the score
  weight: 0.25
  skills: [typescript, nodejs, redis, kafka]

tracked_gaps: [typescript, nodejs, pytest, kafka]   # ranked by the gap heatmap

synonyms:             # the single highest-leverage block for match accuracy
  rag: ["retrieval augmented generation", "retrieval-augmented generation", "rag pipeline"]
  llm: ["large language model", "llms", genai, "generative ai", "foundation model"]
  vector_db: ["vector database", "vector store", pinecone, qdrant, pgvector, faiss]
```

Ids are snake_case and matched intelligently: `rest_api` also matches "REST
API", "rest-api" and "RESTful"; `ci_cd` matches "CI/CD". Two-letter ids like
`go` only match through an alias (`golang`), so ordinary prose — "we go to
production weekly" — never registers as the Go language.

### `profile/filters.yaml`

Gates, rubric weights, verdict bands and the enrichment budget:

```yaml
target_roles:
  primary:   ["AI Engineer", "LLM Engineer", "Machine Learning Engineer"]  # earns the domain bonus
  secondary: ["Backend Engineer", "Python Developer"]

title_must_not_contain: [senior, staff, principal, lead, manager, devops, frontend]

locations:
  accept_onsite: [Bangalore, Hyderabad, Pune, Gurugram, Noida, Delhi]
  accept_remote_scopes: [india, global]
  reject_if_onsite_elsewhere: true

experience:
  max_years_required: 3      # full seniority credit at or below this
  reject_at_or_above: 4      # hard gate
  accept_internships: false
  accept_contract: true

compensation:
  hard_floor_lpa: null       # null = no gate; most Indian listings omit salary
  reference_lpa: 12          # full comp credit at or above this
  reject_unpaid: true

weights:                     # must sum to 100 — `check` warns if they don't
  must_have_skills: 33
  seniority_fit: 15
  adjacent_skills: 13
  ai_domain_bonus: 12
  freshness: 13
  location_mode: 9
  compensation: 5

verdict_bands: { apply_now: 85, strong: 70, worth_a_look: 55 }

enrichment:
  max_jd_fetches_per_run: 15
  min_delay_seconds: 4
  max_delay_seconds: 11
  skip_if_rule_score_below: 45   # don't spend a fetch on a job the rules already rejected
```

### Environment

Everything secret lives in `.env` locally (gitignored) or repository secrets in
CI. Full reference in [`.env.example`](.env.example); the essentials:

| Variable | Required | Notes |
|---|---|---|
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | yes | App Password, **not** your Google password |
| `SHEET_ID` | yes | The long id in the sheet URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes in CI | Whole key file, pasted as a secret |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | local | Path to the key file, kept outside the repo |
| `LLM_ENABLED` / `OPENAI_API_KEY` | optional | AI scoring layer |
| `LLM_BASE_URL` | optional | Any OpenAI-compatible endpoint (Groq, Together, local) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | optional | Morning digest |

---

## Deployment

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs it at
**01:45 UTC (07:15 IST)** every day and handles the operational details that
make an unattended cron job survivable:

- **Heartbeat commit** on every scheduled run, because GitHub disables cron
  workflows in repositories with no activity for 60 days.
- **Permanent JD cache** restored across runs, so each posting costs LinkedIn
  exactly one request, ever.
- **Automatic GitHub issue** on a crash or a zero-yield run, deduplicated so a
  week of failures does not become seven issues.
- **Run log + CSV uploaded** as an artifact for 14 days.
- **Manual dispatch** with a dry-run toggle, so you can test the real environment
  before trusting the schedule.

Self-hosting works too — it is a plain CLI. Any scheduler that can run
`python -m job_radar run` with the right environment will do.

---

## What lands in the sheet

One row per job, appended the morning it appears, highest score first. Columns
are ordered so the first screen answers *should I care?* before you scroll right.

| | | | | |
|---|---|---|---|---|
| `job_id` | `first_seen` | `match_score` | `verdict` | `company` |
| `role_title` | `seniority` | `vacancy_type` | `work_mode` | `location` |
| `posted_at` | `applicants` | `salary` | `matched_skills` | `missing_skills` |
| `why_matched` | `jd_summary` | `apply_url` | `source` | **`status`** · **`notes`** |

`match_score` is frozen four columns in, `status` gets a dropdown
(New → Shortlist → Applied → Interview → Closed), and **`status` and `notes` are
written once, blank, and never touched again.** They are yours.

The sheet is also the pipeline's memory: reading back the `job_id` column is what
prevents duplicates. No state file, no database — and if you delete a row it
simply comes back tomorrow.

---

## What it costs

| Component | Plan | Monthly |
|---|---|---|
| GitHub Actions | ~2 min/day; 2,000 free min/mo private, unlimited public | ₹0 |
| Gmail IMAP · Sheets API · LinkedIn alerts | Included / free tier | ₹0 |
| Rule-based scoring | Runs locally, no external calls | ₹0 |
| OpenAI scoring *(optional)* | ~15 JDs/day × ~2k tokens on `gpt-4o-mini` | ≈ ₹20 |
| **Total** | **Free without AI scoring** | **₹0–20** |

Groq and Gemini both have free tiers generous enough for 15 scoring calls a day —
point `LLM_BASE_URL` at one and the cost is zero.

---

## Design notes

Five failure modes shaped this codebase. Each costs a day to diagnose and a
minute to avoid. Full detail in **[docs/DESIGN.md](docs/DESIGN.md)**.

<details>
<summary><b>1. Google OAuth refresh tokens die after 7 days</b></summary>

With the consent screen left in Testing status, the refresh token expires in a
week and the automation fails silently forever. **Avoided** by using IMAP with an
App Password — no Cloud project, no consent screen, no expiry. Sheets uses a
service account, which has no such limit.
</details>

<details>
<summary><b>2. GitHub runners are datacenter IPs, and LinkedIn knows</b></summary>

Fetching job pages from a runner hits rate limits or HTTP 999 far sooner than
from home broadband. **Designed for**: enrich only the top 15/day, randomized
4–11s delays, every JD cached permanently by `job_id`, and after three
consecutive failures the stage stops and the run degrades to email-only metadata
rather than crashing.
</details>

<details>
<summary><b>3. Scheduled workflows switch off after 60 days</b></summary>

GitHub disables cron in repositories with no commit activity — and a job search
running fine is exactly a repo with no commits. **Fixed** with a one-line
heartbeat commit on every scheduled run.
</details>

<details>
<summary><b>4. GitHub cron is approximate, and UTC-only</b></summary>

Runs are queued, not guaranteed; delays of 10–30 minutes are normal and peak-time
runs are occasionally skipped. **Fixed** by scheduling at an odd minute
(`45 1 * * *`) and making every run idempotent, so a skipped day self-heals
tomorrow instead of leaving a hole.
</details>

<details>
<summary><b>5. LinkedIn will change the alert email's HTML</b></summary>

Not if — when. Your parser returns zero jobs, the sheet stops growing, and you
assume hiring slowed down. **Fixed** with committed `.eml` fixtures, a parser
with fallback selectors and a plain-text fallback, exit code `2` on zero yield,
and an automatic GitHub issue.
</details>

---

## Testing

```bash
pytest -q            # 57 tests, fully offline
pyflakes src tests   # must be silent
```

The suite never touches the network, needs no credentials, and spends nothing —
the OpenAI layer is tested against a fake client that also simulates a
`json_schema` rejection. End-to-end tests are pinned to a fixture profile, so
tuning your own `skills.yaml` can never break them.

**When the parser breaks:**

```bash
python -m job_radar save-fixtures --days 3
python -m job_radar cards --fixtures tests/fixtures
# fix src/job_radar/sources/parse.py, add the assertion, then:
pytest -q
```

Redact the fixture before committing — a real alert email carries your address
and LinkedIn tracking tokens. See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Project layout

```
profile/
  cv.md                       your resume (gitignored) — cv.example.md is the template
  skills.yaml                 four weighted tiers, tracked gaps, synonym map
  filters.yaml                gates, rubric weights, verdict bands, enrichment budget
src/job_radar/
  sources/gmail_linkedin.py   stage 1 — IMAP, App Password, read-only BODY.PEEK
  sources/parse.py            stage 2 — alert HTML to RawCard, text fallback, never raises
  enrich/linkedin_guest.py    stage 3 — public JD fetch, permanent cache, degrades
  score/rules.py              stage 4 — gates + the weighted rubric (free)
  score/llm.py                stage 4b — optional OpenAI layer over the top ~15
  score/vocab.py              baseline skill vocabulary for gap detection
  sinks/sheets.py             stage 5 — dedup on job_id, append, never touch your columns
  sinks/csv_sink.py           local mirror of the same rows
  profile.py                  both YAML files to typed objects
  textutil.py                 normalization, skill matching, dependency-free TF-IDF
  pipeline.py                 the five stages wired together
  cli.py                      entry point
.github/workflows/daily.yml   cron, alarms, heartbeat, cache
docs/DESIGN.md                why the pipeline has this shape
```

No numpy, no scikit-learn, no ORM, no web framework. TF-IDF cosine similarity is
40 lines of standard library — the whole install is seven packages.

---

## Roadmap

Most of these fall out almost free once the sheet has a month of data in it.

- [ ] **Skill-gap heatmap** — aggregate `missing_skills` across everything scored
      above 70. *"Kubernetes appeared in 41% of your strong matches and isn't on
      your CV"* is a ranked learning roadmap derived from the market you are
      actually targeting.
- [ ] **Recalibrate on outcomes** — the `status` column you maintain is training
      data. After ~30 resolved applications, compare callback rate by score band.
      If 60-point jobs convert better than 80-point ones, the weights are wrong,
      and now you can prove it.
- [ ] **Auto-tailored drafts** — for anything 85+, generate a cover letter and
      three rewritten CV bullets mirroring the JD's language.
- [ ] **Ghost-job detector** — flag postings that reappear every few weeks or sit
      open past 45 days. Usually pipeline-building, not hiring.
- [ ] **More sources** — Stage 1 is an interface. Naukri, Adzuna, and
      Greenhouse/Lever/Ashby boards are one adapter each; ATS boards surface roles
      days before aggregators.
- [ ] **Weekly trend tab** — median advertised comp, remote share, most active
      hiring companies, week over week.

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Author

**SK Rohan Parveag** — backend and applied-AI engineer, Kolkata, India.
Builds RAG pipelines, multi-agent and tool-calling systems, and the data
infrastructure underneath them.

[![Website](https://img.shields.io/badge/website-rohanparveag.online-0a6a68)](https://rohanparveag.online)
[![Email](https://img.shields.io/badge/email-parveagr%40gmail.com-EA4335?logo=gmail&logoColor=white)](mailto:parveagr@gmail.com)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-sk--rohan--parveag-0A66C2?logo=linkedin&logoColor=white)](https://linkedin.com/in/sk-rohan-parveag-999294212)
[![ORCID](https://img.shields.io/badge/ORCID-0009--0009--2929--6224-A6CE39?logo=orcid&logoColor=white)](https://orcid.org/0009-0009-2929-6224)
[![GitHub](https://img.shields.io/badge/GitHub-DevRohan33-181717?logo=github&logoColor=white)](https://github.com/DevRohan33)

Also `un_favv__`.

Built for my own job search in Kolkata, then cleaned up so it works for yours.
If it lands you an interview, I would genuinely like to hear about it.

## License

[MIT](LICENSE) © 2026 SK Rohan Parveag

Job Radar reads *your* inbox with *your* credentials and writes to *your* sheet.
It does not scrape LinkedIn, and it is not affiliated with or endorsed by
LinkedIn, Google, or OpenAI.
