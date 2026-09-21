# melody

A custom MCP server and CLI tool to improve AI-generated code and behavior, based on real evidence. Derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

Melody is a code reviewer for code produced by today's most capable LLMs, backed by findings that use real evidence and commands that actually ran against the codebase.

## The Problems

LLM coding agents don't fail the way junior developers fail. They make silent assumptions and run with them, overcomplicate simple problems, touch code they weren't asked to touch, and write tests that pass without verifying anything.

Up until now, the community's response to this has primarily been prompt engineering (e.g. a `CLAUDE.md` file asking the model to behave better). The issue is that this relies on the LLM's own judgement to interpret and review its own work - introducing bias, inconsistency across multi-file projects, and unverifiable tests.

From Andrej's post:

> "The models make wrong assumptions on your behalf and just run along with them without checking. They don't manage their confusion, don't seek clarifications, don't surface inconsistencies, don't present tradeoffs, don't push back when they should."

> "They really like to overcomplicate code and APIs, bloat abstractions, don't clean up dead code... implement a bloated construction over 1000 lines when 100 would do."

> "They still sometimes change/remove comments and code they don't sufficiently understand as side effects, even if orthogonal to the task."

## The Solution

Melody takes another approach. Instead of instructing a model to be careful, it inspects the code diff and proves what went wrong by executing a command: a `git grep`, an AST walk, a test run. Findings are evidence, not opinions.

Inspired by Andrej's post, I categorized these LLM pitfalls into four main pillars to directly address these issues:

| Principle                 | Addresses                                                 |
| ------------------------- | --------------------------------------------------------- |
| **Think Before Coding**   | Wrong assumptions, hidden confusion, missing tradeoffs    |
| **Simplicity First**      | Overcomplication, bloated abstractions                    |
| **Surgical Changes**      | Unrelated edits, touching code you shouldn't              |
| **Goal-Driven Execution** | Leverage through tests-first, verifiable success criteria |

Every pillar is implemented through specific checks, commands, evidence, and success criteria. Three run against a diff via the CLI; one runs inside the agent loop via MCP.

## The evidence rule

Every finding carries an evidence tier, and the tier is enforced in the type system, not by convention:

| Tier       | Meaning                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `proven`   | A command ran and produced this output. The command and its output are attached to the finding. |
| `inferred` | Derived from static analysis of the diff or AST, not from a command's output.                   |
| `advisory` | Human judgment, clearly labeled as such.                                                        |

`Evidence` refuses construction if a `proven` finding has no command attached. During development, this constraint actually caught two real bugs in Melody's own checks before they shipped!

## The Four Principles, in detail:

### Think Before Coding — `TC001`, `TC002` (MCP only)

This pillar can't be checked "after the fact" — a finished diff doesn't contain the model's assumptions (that it probably never surfaced anyway). So it runs _before_ code exists, called by the agent mid-task, during the loop:

- **`TC001_assumption_gap`** — compares what the model's plan commits to (a format, a library, a data structure, an error behavior) against what the developer's task actually specified. Anything ungrounded is a silent assumption. **`inferred`.**

- **`TC002_missing_reasoning_structure`** — when real gaps exist, it requires at least two named approaches with stated tradeoffs on record. **`inferred`.**

TC002 confirms that the reasoning structure was filled in, not that the reasoning inside it was good. That limitation is intentional and stated rather than hidden.

### Simplicity First — `SF001`, `SF002`, `SF003`

"Would a senior engineer or an LLM call this code overcomplicated?" is a judgment call. A cyclomatic complexity score is a number a tool computed. Melody only ships the second kind.

- **`SF001_single_use_abstraction`** — a function or class with exactly one call site in the whole repo. Evidence: the `git grep` call-site count. **`proven`.**

- **`SF002_excess_complexity`** — cyclomatic complexity computed by AST walk (counting `if`/`for`/`while`/`except`/boolean-operator nodes) for functions touched by the diff, flagged above a threshold of 10. Evidence: the score and a branch-by-branch tally. **`inferred`.**

- **`SF003_extraneous_file`** — a new `config/`, `utils/`, `types/`, or `constants/` catch-all file, confirmed as newly added via `git diff --diff-filter=A`. **`proven`.**

### Surgical Changes — `SC001_orphaned_symbol`

Detects two ways that symbols removed or renamed by the diff can leave the repo broken:

- A removed symbol still referenced elsewhere (a broken reference)
- The diff removed the last call site of a symbol that still exists (an orphaned definition)

Candidates are found by scanning removed diff lines, then confirmed by parsing the pre-diff file's AST — so a match inside a comment or string can't produce a false positive. References are searched with `git grep`, which only scans tracked files. **Evidence: `proven`.**

### Goal-Driven Execution — `GD001_test_does_not_reproduce_bug`

Detects a "fix" whose accompanying test doesn't actually fail against the pre-fix code — meaning the test doesn't reproduce the bug it claims to catch. This check reconstructs the base commit's state, grafts in the new test, and runs it. If it passes against the buggy code, that's the finding. **Evidence: `proven`** — the pytest command and its real output.

The idea is to transform imperative instructions into declarative goals with verification loops and detailed checks.

## Installation

Requires Python 3.14+ and git.

```bash
git clone https://github.com/betancourt-ncs/melody.git
cd melody
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Verify with:

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

Melody also runs as an MCP server, so an agent can call it mid-task rather than only reviewing finished work. It exposes two tools:

- `review_diff(repo_path, diff_ref)` — the same checks as the CLI
- `check_assumptions(task_description, planned_approach)` — the Think Before Coding pillar

First install the MCP extra:

```bash
pip install -e '.[mcp]'
```

Register it as a custom MCP server with:

```
Command:   /absolute/path/to/melody/.venv/bin/python
Arguments: /absolute/path/to/melody/mcp_server.py
```

Then, from inside an agent session, tell the LLM something like:

> "Before you commit to that approach, use Melody to check your assumptions."

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

## Is it working?

Each pillar is doing its job if you can verify that:

- **Melody flags an assumption before code ships, not after** — a silent format or library choice gets timely caught, not neglected after mistakes have been made
- **A complexity rating backs up "this is complicated," instead of a guess** — the score and the branches that produced it are both shown
- **Only what the diff actually deleted gets flagged** — untouched code never shows up in a finding
- **A "fix" only passes review if its test actually fails on the old, broken code** — not just on the new one

## Built for the Miami AI Hackathon

Built with [Mel](https://openmel.dev) on September 18–19, 2026, for the Agentic Engineering track.

- Update: This project received an Honorable Mention award 😎 ⭐️

Check out `evidence/runlog.md` for my honest build record — every task, every bug, every point where Mel needed correction. If you don't want to read every single entry, here are two entries I think are worth reading: the `GD001` `diff_ref` convention bug, where the check's own unit tests passed green while it was silently broken through the real CLI path (the exact failure mode `GD001` in my tool exists to catch). Also, the `TC002` evidence-tier conflict entry, where the type system's `proven` rule forced a design decision mid-build, which correctly awaited my judgment before choosing which approach to take.
