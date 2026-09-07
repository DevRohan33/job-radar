# Working on this repository

Design rationale and the failure modes this pipeline is built around:
**[docs/DESIGN.md](docs/DESIGN.md)**. Read it before changing a stage.

## Conventions

- **Policy lives in YAML, not Python.** Skill tiers, gates, rubric weights,
  verdict bands and the enrichment budget all come from `profile/*.yaml` via
  `src/job_radar/profile.py`. A hardcoded threshold, title or weight is a bug.
- **Every optional stage degrades.** Enrichment, AI scoring and the digest must
  never fail a run — catch, log, fall back to what the previous stage produced.
- **The parser is the fragile part.** `sources/parse.py` is the only code
  LinkedIn can break unilaterally. Change it only against a real `.eml` fixture,
  and add the assertion in the same commit.
- **Never write to the user's columns.** `status` and `notes` are seeded once
  and never touched again.
- Type hints throughout. Docstrings explain *why*, not *what*.

## Before you finish

```bash
pytest -q            # 57 tests, fully offline
pyflakes src tests   # must be silent
```

## Do not commit

`profile/cv.md`, `.env`, service-account JSON, or an unredacted alert-email
fixture — a real one carries the recipient's address and LinkedIn tracking
tokens.
