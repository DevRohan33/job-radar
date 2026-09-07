# Job Radar — build plan

A scheduled pipeline that reads LinkedIn's own job-alert emails every morning,
scores each opening against your CV, and appends the ranked result to one Google
Sheet.

- **Source** — LinkedIn alert emails via Gmail IMAP
- **Runtime** — Python on GitHub Actions
- **Sink** — Google Sheets
- **Cadence** — daily, 07:15 IST

Implementation status: built. See `README.md` for usage and `SETUP.md` for
credentials.

---

## 01 · Why this shape

**LinkedIn cannot be scraped reliably, and shouldn't be.** Automated access
breaks their terms, and a logged-in scraper risks restriction on the one account
a job search depends on. But LinkedIn will happily *email* matching jobs every
day, for free, via saved searches with alerts turned on. That email is a
legitimate, stable, permission-granted feed — you are reading your own inbox,
not fighting the platform.

**A chat subscription is not an API key.** ChatGPT Plus or Claude Pro covers the
chat app, not programmatic API access, which is billed separately. A script
running on GitHub Actions at 07:15 cannot use either plan. That's fine: the
rule-based scorer is free, and the optional OpenAI layer costs a few rupees a
month at this volume.

**What alert emails don't carry: the full job description.** They give title,
company, location and a link. Skills matching against a title alone is shallow,
so the pipeline fetches the public job page for a small ranked shortlist each
day — 10 to 15 — rather than hammering LinkedIn with a hundred requests.

---

## 02 · The pipeline

Five stages, once a day. Each hands a normalized record to the next, so any
stage can be swapped without touching the others.

| Stage | Does | Key tools |
|---|---|---|
| 1 Collect | Connect to Gmail over IMAP, pull yesterday's LinkedIn alerts | `imaplib`, App Password |
| 2 Parse | Extract each job card from the email HTML, resolve links, normalize | `selectolax`, `pydantic` |
| 3 Filter & enrich | Drop hard-fails, cheap-rank the rest, fetch full JDs for the top slice | rules, `httpx`, cache |
| 4 Score | Compare each JD to the skills profile: 0–100, matched, gaps, one-line reason | rubric, optional LLM |
| 5 Publish | Dedup against job ids already in the sheet, append new rows, sort by score | `gspread`, service account |

The sheet is also the pipeline's memory. Reading back the `job_id` column at the
start of Stage 5 is what prevents duplicates — no state file to commit, no
database to run, and a row you delete simply comes back tomorrow.

### Repository layout

```
job-radar/
├─ .github/workflows/daily.yml      # cron: "45 1 * * *" (01:45 UTC = 07:15 IST)
├─ profile/
│  ├─ cv.md                         # your resume as plain text
│  ├─ skills.yaml                   # core/strong/familiar/learnable tiers + synonyms
│  └─ filters.yaml                  # gates, rubric weights, bands, enrichment budget
├─ src/job_radar/
│  ├─ sources/gmail_linkedin.py     # Stage 1
│  ├─ sources/parse.py              # Stage 2
│  ├─ enrich/linkedin_guest.py      # Stage 3
│  ├─ profile.py                    # both YAML files -> typed objects
│  ├─ score/{rules.py, llm.py}      # Stage 4
│  ├─ sinks/{sheets.py, csv_sink.py}# Stage 5
│  └─ models.py                     # the Job record every stage speaks
└─ tests/fixtures/*.eml             # saved real alert emails — the parser's safety net
```

---

## 03 · What lands in the sheet

One row per job, appended the morning it appears. Columns are ordered so the
first screen answers "should I care?" before you scroll right for detail.

| Column | Type | Notes |
|---|---|---|
| `job_id` | text | LinkedIn's numeric id. The dedup key — never blank |
| `first_seen` | date | Run date it entered the sheet |
| `match_score` | 0–100 | Frozen at 3 columns in, visible without scrolling |
| `verdict` | enum | Apply now / Strong / Worth a look / Archive |
| `company` | text | |
| `role_title` | text | As posted |
| `seniority` | enum | Intern / Junior / Mid / Senior / Staff+ |
| `vacancy_type` | enum | Full-time / Contract / Internship / Part-time |
| `work_mode` | enum | Remote / Hybrid / On-site |
| `location` | text | City, country as posted |
| `posted_at` | date | Age matters — see the freshness weight |
| `applicants` | number | When shown. Competition signal |
| `salary` | text | Normalized to LPA where a range is given |
| `matched_skills` | list | Your skills the JD asks for |
| `missing_skills` | list | Required skills you don't list. Feeds the gap analysis |
| `why_matched` | text | One sentence. The column that makes the score trustworthy |
| `jd_summary` | text | ~40-word compression of the description |
| `apply_url` | link | Clean canonical URL, tracking stripped |
| `source` | text | Which alert produced it — tells you which searches earn their keep |
| `status` | dropdown | **Yours.** New → Shortlist → Applied → Interview → Closed. Never overwritten |
| `notes` | text | Also yours. Also never overwritten |

---

## 04 · How the score is built

A single number is only useful if you can see inside it. The rubric is explicit,
weighted, and written to the sheet alongside the reasons — so when it's wrong
you can tell exactly which weight to change.

**Gate first, then weigh.** A job is rejected outright if it fails any hard
filter in `filters.yaml`: a blocked title term, on-site in a city you won't move
to, more years of experience than your ceiling, an internship, an unpaid role,
or a blocklisted company. Gates keep noise out; weights rank what's left.

Both the gates and the weights live in YAML, so retuning the rubric never means
touching Python.

| Weight | Component | What it measures |
|---:|---|---|
| 33 | `must_have_skills` | Core-tier overlap, plus how much of the JD's stack you own (tier-weighted) |
| 15 | `seniority_fit` | Years required vs your ceiling, and the inferred band |
| 13 | `adjacent_skills` | Familiar + learnable hits, plus CV↔JD similarity |
| 13 | `freshness` | Age of the posting × applicant count |
| 12 | `ai_domain_bonus` | Primary target-role title, plus AI-signal skills in the JD |
| 9 | `location_mode` | Position in your `work_mode_preference` order |
| 5 | `compensation` | Advertised comp against `reference_lpa` |

→ 85–100 Apply today · 70–84 Strong, tailor CV · 55–69 Worth a look · <55 Archive

Freshness is weighted deliberately: a role posted four days ago with 200
applicants is a worse bet than a 65-point match posted this morning. The score
is about *where to spend today's hour*, not abstract fit.

**Two ways to compute it.** Rule-based (free): normalized skill-token matching
against `skills.yaml` with a synonym map, so `React.js`, `ReactJS` and `React`
collapse to one skill, plus TF-IDF similarity between the JD and your CV. Good
for perhaps 80% of what matters, at no cost. LLM-assisted (optional): the rule
score prefilters to the top ~15, and only those go to a model returning
structured JSON (OpenAI strict schema mode). This is what catches "they want
distributed systems experience and your CV describes it without ever using that
phrase."

---

## 05 · Build order

Ship the fragile part first. The email parser is the only component that can
break without warning, so it goes to production on day one where you'll notice.

| Phase | Scope | Output |
|---|---|---|
| 0 · your setup (~40 min, manual) | 3–5 LinkedIn saved searches with daily alerts; 2-Step Verification + Gmail App Password; Google Cloud project, Sheets API, service account, sheet shared as Editor; CV into `profile/cv.md` | Credentials in hand, alerts flowing |
| 1 · inbox to sheet, no scoring | Stages 1, 2, 5 only, run locally against a week of real alert emails saved as fixtures | A sheet filling daily with real, unique jobs |
| 2 · filters, rule scoring, automation | `filters.yaml` gates, the free scorer, GitHub Actions on the daily cron | It runs without you, every morning, for free |
| 3 · JD enrichment and AI scoring | Full descriptions for the shortlist, with caching, backoff and graceful degradation; the LLM layer on top of the rule prefilter | Real skill matching, real reasons, real gaps |
| 4 · the compounding layer | Morning digest, skill-gap analytics, tracker feedback loop | The system starts teaching you about the market |

---

## 06 · Where it gets interesting

Everything above gets a good list. These make the system worth more than the
list, and most fall out almost free once the data accumulates.

- **Skill-gap heatmap** *(highest value)* — aggregate `missing_skills` across
  every job scored above 70. After a month: "Kubernetes appeared in 41% of your
  strong matches and isn't on your CV." A ranked learning roadmap derived from
  the market you're actually targeting.
- **Recalibrate on your own outcomes** *(closed loop)* — the `status` column is
  training data. Once ~30 applications resolve, compare callback rate by score
  band. If 60-point jobs convert better than 80-point ones, the weights are
  wrong, and now you can prove it.
- **Auto-tailored application drafts** — for anything 85+, generate a cover
  letter and three rewritten CV bullets mirroring the JD's language.
- **Ghost-job detector** — flag postings whose title and company reappear every
  few weeks, or that sit open past 45 days. Usually pipeline-building.
- **Company intelligence** — enrich each company once: headcount trend, last
  funding, layoff news, Glassdoor. Cached indefinitely.
- **More sources, same pipeline** — Stage 1 is an interface. Adzuna, Jooble,
  Greenhouse/Lever/Ashby boards, Naukri alerts: one adapter each, scoring and
  sheet untouched. ATS boards surface roles days before aggregators.
- **Morning digest** — Telegram or email at 08:00 with the top five. The
  difference between a system you use and one you abandon in week three.
- **Weekly trend tab** — median advertised comp, share of remote postings, most
  active hiring companies, week over week.
- **Self-monitoring** — if a run parses zero jobs, or yield drops more than half
  against the trailing 7-day average, open a GitHub issue automatically. Silent
  failure is how these pipelines actually die.

---

## 07 · Traps worth knowing

1. **Google OAuth refresh tokens die after 7 days.** With the consent screen in
   Testing status, the refresh token expires in a week and the automation fails
   silently forever. *Avoided:* IMAP with an App Password — no Cloud project, no
   consent screen, no expiry. Sheets still uses a service account; that path has
   no such limit.
2. **GitHub's runners are datacenter IPs, and LinkedIn knows.** Fetching job
   pages from a runner hits rate limits or HTTP 999 far sooner than from home
   broadband. *Designed for:* enrich only the top 10–15/day, randomize delays,
   cache every JD permanently by `job_id`, degrade to email-only metadata rather
   than crashing. If it becomes a real problem, move only that stage to a
   self-hosted runner.
3. **Scheduled workflows get switched off after 60 days** of no commit activity
   — and a job search running fine is exactly a repo with no commits. *Fixed:*
   the workflow commits a one-line heartbeat to `state/` on each run.
4. **GitHub cron is approximate, and UTC-only.** Delays of 10–30 minutes are
   normal and peak-time runs are occasionally skipped. *Fixed:* schedule at an
   odd minute (`45 1 * * *`), and make every run idempotent so a skipped day
   self-heals tomorrow.
5. **LinkedIn will change the alert email's HTML.** Not if — when. *Fixed:*
   real alert emails committed as fixtures, a parser with fallback selectors and
   a plain-text fallback, and the zero-yield alarm wired on day one.

---

## 08 · What it costs

| Component | Plan | Monthly |
|---|---|---|
| GitHub Actions | ~2 min/day; free tier 2,000 min/mo private, unlimited public | ₹0 |
| Gmail IMAP | Included | ₹0 |
| Google Sheets API | Well under free quota | ₹0 |
| LinkedIn alerts | Free account feature | ₹0 |
| Rule-based scoring | Local, no external calls | ₹0 |
| LLM scoring *(optional)* | ~15 JDs/day × ~2k tokens on OpenAI `gpt-4o-mini` | ≈ ₹20 |
| **Total** | Free without AI scoring | **₹0–90** |

To keep it at zero, Groq and Gemini both have free tiers generous enough for 15
scoring calls a day. `score/llm.py` speaks the OpenAI API, so any
OpenAI-compatible endpoint is just `LLM_BASE_URL` — a config change, not a
rewrite.

---

## 09 · To start using it

1. **Your CV** — becomes `profile/cv.md` and the skills profile everything else
   scores against.
2. **Your filters** — target roles and seniority, acceptable locations and work
   modes, minimum comp, companies to exclude.
3. **A blank Google Sheet** plus the Phase 0 saved searches, so alerts start
   landing.

`python -m job_radar check` verifies all three plus every credential.

---

*Prices and platform behaviours verified September 2026. LinkedIn's email
format, GitHub's scheduling behaviour and API pricing all drift — the traps
above are written on the assumption that they will.*
