# melody

A code reviewer for AI-generated code. Every finding is backed by a command that actually ran.

LLM coding agents don't fail the way junior developers fail. They make silent assumptions and run with them, overcomplicate simple problems, touch code they weren't asked to touch, and write tests that pass without verifying anything. Andrej Karpathy [wrote about this](https://x.com/karpathy/status/2015883857489522876), and the community response has largely been prompt engineering — a `CLAUDE.md` file asking the model to behave better.

melody takes the other approach. Instead of instructing a model to be careful, it inspects the diff and proves what went wrong by executing something: a `git grep`, an AST walk, a test run. Findings are evidence, not opinions.

## The evidence rule

Every finding carries an evidence tier, and the tier is enforced in the type system — not by convention:

| Tier       | Meaning                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `proven`   | A command ran and produced this output. The command and its output are attached to the finding. |
| `inferred` | Derived from static analysis of the diff or AST, not from a command's output.                   |
| `advisory` | Human judgment, clearly labeled as such.                                                        |

`Evidence` refuses construction if a `proven` finding has no command attached. During development this constraint caught two real bugs in melody's own checks before they shipped.

## Checks

melody implements four of Karpathy's pillars. Three run against a diff via the CLI; one runs inside the agent loop via MCP.

### Surgical Changes — `SC001_orphaned_symbol`

Detects symbols removed or renamed by the diff that left the repo broken:

- A removed symbol still referenced elsewhere (a broken reference)
- The diff removed the last call site of a symbol that still exists (an orphaned definition)

Candidates are found by scanning removed diff lines, then confirmed by parsing the pre-diff file's AST — so a match inside a comment or string can't produce a false positive. References are searched with `git grep`, which only scans tracked files. **Evidence: `proven`.**

### Goal-Driven Execution — `GD001_test_does_not_reproduce_bug`

Detects a "fix" whose accompanying test doesn't actually fail against the pre-fix code — meaning the test doesn't reproduce the bug it claims to catch. Reconstructs the base commit's state, grafts in the new test, and runs it. If it passes against the buggy code, that's the finding. **Evidence: `proven`** — the pytest command and its real output.

### Simplicity First — `SF001`, `SF002`, `SF003`

- **`SF001_single_use_abstraction`** — a function or class with exactly one call site in the whole repo. Evidence: the `git grep` call-site count. **`proven`.**
- **`SF002_excess_complexity`** — cyclomatic complexity computed by AST walk (counting `if`/`for`/`while`/`except`/boolean-operator nodes) for functions touched by the diff, flagged above a threshold of 10. Evidence: the score and a branch-by-branch tally. **`inferred`.**
- **`SF003_extraneous_file`** — a new `config/`, `utils/`, `types/`, or `constants/` catch-all file, confirmed as newly added via `git diff --diff-filter=A`. **`proven`.**

"Would a senior engineer call this overcomplicated?" is a judgment call. A cyclomatic complexity score is a number a tool computed. melody only ships the second kind.

### Think Before Coding — `TC001`, `TC002` (MCP only)

This pillar can't be checked after the fact — a finished diff doesn't contain the assumption the model never surfaced. So it runs _before_ code exists, called by the agent mid-task:

- **`TC001_assumption_gap`** — compares what the plan commits to (a format, a library, a data structure, an error behavior) against what the task actually specified. Anything ungrounded is a silent assumption. **`inferred`.**
- **`TC002_missing_reasoning_structure`** — when real gaps exist, requires at least two named approaches with stated tradeoffs on record. **`inferred`.**

TC002 proves the reasoning structure was filled in, not that the reasoning inside it was good. That limitation is intentional and stated rather than hidden.

## Installation

Requires Python 3.14+ and git.

```bash
git clone https://github.com/betancourt-ncs/melody.git
cd melody
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify:

```bash
melody --help
pytest -q          # 21 passed
```

## Usage

### CLI

Review the most recent commit in any git repo:

```bash
melody review --diff HEAD~1
```

JSON output, for piping into other tools:

```bash
melody review --diff HEAD~1 --format json
```

Review against any base ref:

```bash
melody review --diff <commit-sha>
```

Exit code is `1` when findings are present, `0` when clean.

A zero-finding report is never presented as a clean bill of health — the report states what it did and did not check.

### MCP server

melody also runs as an MCP server, so an agent can call it mid-task rather than only reviewing finished work. It exposes two tools:

- `review_diff(repo_path, diff_ref)` — the same checks as the CLI
- `check_assumptions(task_description, planned_approach)` — the Think Before Coding pillar

Register it as a custom MCP server with:

```
Command:   /absolute/path/to/melody/.venv/bin/python
Arguments: /absolute/path/to/melody/mcp_server.py
```

Requires the MCP extra:

```bash
pip install -e '.[mcp]'
```

Then, from inside an agent session:

> Before you commit to that approach, use melody to check your assumptions.

## Architecture

Three layers, strictly separated. The core knows nothing about how it's invoked.

```
core/                    # check logic — plain Python, no CLI or MCP awareness
  models.py              # Finding, Evidence, EvidenceTier (the evidence rule lives here)
  diff.py                # unified diff parsing
  pipeline.py            # review_diff() — the single entry point both adapters call
  checks.py              # the CHECKS registry
  orphan_check.py        # SC001
  test_verification_check.py  # GD001
  single_use_check.py    # SF001
  complexity_check.py    # SF002
  extraneous_files_check.py   # SF003
  think_before_coding.py # TC001/TC002 (MCP only, not in CHECKS)
cli.py                   # thin adapter
mcp_server.py            # thin adapter
evidence/runlog.md       # build log: what was attempted, what failed, what was corrected
```

Logic is never duplicated between adapters — both call the same core.

## Scope and known limitations

Declared operating boundaries, stated rather than discovered:

- **Python repos only. Git repos only.** Operates on a diff.
- **Dynamic access is invisible.** `getattr(obj, "name")` and other reflective patterns can't be traced by AST analysis. Out of scope.
- **`TC001` format detection is a fixed list** — `json`, `yaml`, `toml`, `xml`, `csv`, `protobuf`, `msgpack`, `parquet`. Other serialization formats (e.g. `pickle`) aren't currently recognized as assumption candidates.
- **`SC001` call-site matching is coarse** — `obj.method()` matches on `method`, so common method names can produce false "still referenced" results.
- **`SF001`/`SF002`/`SF003` were verified against constructed fixture commits**, not organically occurring ones.
- **Not checked:** whether one proposed approach is simpler than another, whether an abstraction is "just a wrapper," and other rules that require judging intent rather than counting something. See `PROJECT.md` for the full future-checks list and the reasoning behind each exclusion.

## Built for the Miami AI Hackathon

Built with [Mel](https://openmel.dev) on September 18–19, 2026, for the Agentic Engineering track.

`evidence/runlog.md` is the honest build record — every task, every bug, every point where Mel needed correction. Two entries worth reading: the `GD001` `diff_ref` convention bug, where the check's own unit tests passed green while it was silently broken through the real CLI path (the exact failure mode `GD001` exists to catch), and the `TC002` evidence-tier conflict, where the type system's `proven`-requires-a-command rule forced a design decision mid-build.
