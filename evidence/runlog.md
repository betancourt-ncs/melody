# mel runlog

One entry per task. Honest by requirement: failures, wrong turns and human
corrections are recorded with the same care as successes. No number here is
invented; where a duration could not be measured it says so.

Timestamps are local (UTC-04:00). Durations are wall-clock between filesystem
and git timestamps, read from `git log --pretty=%cI` and `stat`.

---

## Task 1 — PROJECT.md + three-layer skeleton

**Date:** 2026-09-18
**Requested:** Write the problem statement, evidence rule, scope and architecture
into `PROJECT.md`; scaffold the three-layer skeleton with empty signatures and a
working CLI; no check logic.
**Done-when:** `mel review --diff HEAD~1` runs against this repo and prints a
report with zero findings without crashing.

### What was attempted, in order

**Phase 0 — Recon and plan (duration: NOT MEASURED).**
Directories inspected, GitHub repo queried, interpreters probed, plan written and
approved. I have no reliable start timestamp for this phase in the environment,
so I am not guessing at one. The first verifiable event is `git init`.

**Phase 1 — git init and PROJECT.md (22:07:58 → 22:08:16, ~20s).**

- `git init -b main`. Repo was empty and untracked; existing `user.name` /
  `user.email` were picked up, no config written.
- `PROJECT.md` written at 22:07:58; committed as `414dbd1` at 22:08:16.
- Also added `.gitignore` in this commit (`.venv/`, `__pycache__/`, `*.pyc`,
  `.pytest_cache/`, `*.egg-info/`). Slight scope addition beyond the literal
  request — flagged here rather than left silent.

**Phase 2 — Skeleton (22:09:00 → 22:12:35, ~3m35s).**
Files written in this order, with mtimes: `pyproject.toml` 22:09:00 →
`core/models.py` 22:11:21 → `cli.py` 22:11:37 → `mcp_server.py` 22:11:40 →
`tests/test_smoke.py` 22:12:29. Committed as `400e2b6` at 22:12:35. 857 lines
across 9 files.

**Phase 3 — Environment and verification (22:11:56 → 22:14, ~2m).**
`.venv` created with the 3.14.2 framework interpreter at 22:11:56;
`pip install -e '.[dev]'` succeeded first try. `pytest` 9 passed in 1.10s. All
six planned verifications run (results below).

### What succeeded

- `PROJECT.md` committed as the governing document.
- Two real commits, so `HEAD~1` resolves honestly: `414dbd1` → `400e2b6`.
- All 9 smoke tests pass (`0.9` for the CLI-subprocess cases, `1.10s` total).
- Every planned verification passed. Exact commands and observed output are in
  the summary delivered to the user for this task; the load-bearing ones:
  - `mel review --diff HEAD~1` → `0 findings.`, exit 0.
  - `mel review --diff not-a-real-ref` → exit 2, message on stderr, no traceback.
  - `mel review --diff HEAD~1` in a non-git dir → exit 2, no traceback.
  - `mel review --diff HEAD~1 --format json` → valid JSON, `findings: []`.
  - `grep` confirms `cli.py` does not import `mcp` or `mcp_server`, and `core/`
  imports neither adapter. Both modules import cleanly with the MCP SDK absent.

### What failed, and what I had to correct

1. **My test helper had a bug I introduced.** `run_mel()` called
   `_mel_executable(cwd)`, but `_mel_executable()` takes no argument — a
   `TypeError` on every subprocess test. Caught by re-reading the test file
   before running it rather than after. One edit to fix. This was my error, not
   a project discovery.

2. **A plan assumption was wrong.** I told the user only Python 3.9.6 was
   available and that the MCP SDK would therefore be unrunnable, and recommended
   installing 3.12. That was wrong: probing during the plan phase was blocked
   from running shell commands by plan mode, so I had only checked
   `python3.x` on PATH. Once execution started I found
   `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` — Python
   **3.14.2** with pip 26.0.1 and a working `venv`. No install was needed and
   the recommendation was moot. The lesson recorded: in plan mode, an
   "only X is installed" claim is a guess unless `/Library/Frameworks` and
   `/opt/homebrew` were actually listed. I was explicit to the user that the
   Homebrew check had been blocked rather than pretending it had passed, which
   is the only reason this was catchable at all.

3. **No check logic exists, so a "clean" report is vacuous.** Handled by making
   `Report.notes` state plainly that no checks are registered and that the run
   read no files. A zero-finding report from this build must not read as a clean
   bill of health.

### Points where the human had to intervene or correct

- **None during execution.** The single human decision point was the approval of
  the proposed file tree and plan; the user approved it as written, with no
  corrections requested. One clarification was requested by me and answered by
  silence: whether `Finding` should refuse construction with zero evidence. I
  added it, since the plan named it and PROJECT.md states it as the core
  constraint. Recorded here so the user can veto it.
- One deviation from a stated working rule, flagged rather than hidden: the
  request said "show me the full file tree and wait for my approval before
  creating any files". The tree was shown and approved via plan mode first, so
  the rule was honored; but `.gitignore` and `tests/` were additions not named
  in the request, and both are called out above rather than buried.

### Open items carried into the next task

- `Finding` rejects empty evidence and `PROVEN` evidence without a command —
  added on my own judgment, awaiting the user's confirmation.
- `core/diff.py:parse_unified_diff`, `ParsedDiff.added_lines` and
  `core/pipeline.py:build_context` raise `NotImplementedError` by design. They
  are unreachable while `CHECKS` is empty.
- `mcp_server.py` is unexercised against a real MCP client: the `mcp` SDK is not
  installed (it is an optional extra), so `FastMCP` registration has never been
  run. `review_diff_tool` itself is exercised and returns a correct `ok: False`
  payload for a bad ref.
- `requires-python = ">=3.10"` in `pyproject.toml`, but only 3.14.2 was actually
  tested. 3.10–3.13 are untested.
