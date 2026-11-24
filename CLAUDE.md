# Dedupe project

Read [PROGRESS.md](PROGRESS.md) first in every session: where the work stands, the open decisions, the working agreements and how to work on
this server. The history with every decision and its reasons is [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md); code comments refer to
its numbered sections. [README.md](README.md) is the reference for installing and using the platform.

Rules for this project:
- Keep everything portable, modular and containerisable.
- Work step by step: a short plan, the work, a short report. Explain what, why and how in plain language (the user is new to software
  engineering). Keep chat output short.
- Be honest about weaknesses: all results are on SYNTHETIC data and do not prove real-world accuracy.
- After every step:
  - add a numbered section to docs/DEVELOPMENT_LOG.md;
  - update PROGRESS.md;
  - commit.
- Confirm with the user before starting a new phase.
- Tests run in Docker: `docker build -q --target test -t dedupe-test .`, then pytest and `python -m ruff check src tests` with `src`,
  `tests` and `configs` mounted. Both must be clean before committing.
