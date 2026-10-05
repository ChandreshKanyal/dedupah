# Dedupe project

Read [PROGRESS.md](PROGRESS.md) first in every session, starting with the **RESUME HERE** block at the top: it states what is done, what is in flight, the next steps, known gaps, working agreements with the user and practical pitfalls. Then the roadmap (section 5) and the per-phase sections (9-18). [README.md](README.md) has the run commands.

Rules for this project:
- Everything must stay portable, modular and containerisable (Docker is set up but still unverified).
- Work step by step: short plan, do the work, short report; explain what/why/how in plain language (the user is new to deduplication). Keep chat output short.
- Be honest about weaknesses; all current results are on SYNTHETIC data and do not prove real-world accuracy.
- After every step: update PROGRESS.md (tick roadmap, decisions log, session log, results sections) and commit. Confirm with the user before starting a new phase.
- Develop and test in the project venv: `.venv/Scripts/python -m pytest -q` and `.venv/Scripts/python -m ruff check src tests` must both be clean before committing.
