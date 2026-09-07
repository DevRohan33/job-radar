# Contributing

Issues and pull requests are welcome. The most useful contributions, in order:

1. **A broken-parser fix with a fixture.** LinkedIn changes its alert email HTML
   without warning. If yours stopped parsing, that is a real bug — see below.
2. **A new source adapter.** Stage 1 is an interface, not a Gmail reader.
   Naukri, Indeed alerts, Greenhouse/Lever/Ashby boards all fit behind it.
3. **Scoring calibration.** If the rubric ranks something obviously wrong, an
   issue with the job description and the score breakdown is enough.

## Setup

```bash
git clone https://github.com/DevRohan33/job-radar.git
cd job-radar
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp profile/cv.example.md profile/cv.md
pytest -q
```

The whole suite runs offline — no credentials, no network, no API spend.

## Fixing the parser

Never guess at the HTML. Capture a real email and work against it:

```bash
python -m job_radar save-fixtures --days 3      # writes .eml into tests/fixtures/
python -m job_radar cards --fixtures tests/fixtures
```

**Redact before committing a fixture.** A real alert email contains your email
address and tracking tokens tied to your LinkedIn account. Replace them, then
add the expectations to `tests/test_parse.py` in the same commit — a fixture
without an assertion protects nothing.

## House style

- Follow the surrounding code: type hints, module docstrings that say *why*,
  comments only where the reason is not obvious from the code.
- Every stage degrades rather than crashes. Enrichment, AI scoring and the
  digest are all optional; if one fails the run still publishes.
- Policy belongs in `profile/*.yaml`, not in Python. If a change hardcodes a
  threshold, a title, or a weight, it belongs in the YAML schema instead.
- New behaviour needs a test. `pytest -q` must be green, and `pyflakes src tests`
  must be silent.

## Commit messages

Imperative mood, one line, scope first where it helps:

```
parse: handle the two-column card layout
score: read verdict bands from filters.yaml
```
