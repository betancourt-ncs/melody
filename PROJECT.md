# melody — a code reviewer/auditor for AI-generated code

This document governs every session on this project. If a future task conflicts with
what is written here, this document wins. Changes to it require an explicit human
decision, not an inferred one.

## Problem

This project is built around Andrej Karpathy's observations on LLM coding. The
pathologies we are targeting:

- Models make wrong assumptions on your behalf and run with them without checking.
  They don't manage their confusion, don't seek clarification, don't surface
  inconsistencies, don't present tradeoffs, don't push back when they should.
- They overcomplicate code and APIs, bloat abstractions, don't clean up dead code,
  and will write 1000 lines where 100 would do.
- They change or remove comments and code they don't sufficiently understand as side
  effects, even when orthogonal to the task.

## The four pillars

Every check exists to serve one of these. Every finding names the pillar it belongs to.

1. **Think Before Coding** — wrong assumptions run with silently; confusion not
   surfaced; tradeoffs not presented; no pushback where pushback was warranted.
2. **Simplicity First** — overcomplicated code and APIs, bloated abstractions, dead
   code left behind, 1000 lines where 100 would do.
3. **Surgical Changes** — comments and code changed or removed as a side effect,
   orthogonal to the stated task.
4. **Goal-Driven Execution** — the change is not tied to a verifiable goal; there is
   no way to tell whether it did what it claimed.

Each pillar has its own checks, details, verifiable sources, and practical
application. The pillar list is fixed; the checks under each pillar are not.

## What we are building

**Detection through execution.** A tool that inspects a diff of AI-generated code and
reports the specific pathologies above, where every finding is backed by something we
actually ran.

## The evidence rule

This is the core constraint of the entire project.

**No finding is ever an opinion.** Every finding carries evidence produced by executing
or statically analyzing something real, and is tagged with its evidence tier:

- **proven** — a command ran and produced this output (e.g. the function has zero call
  sites; the branch is uncovered by the test suite).
- **inferred** — derived from static analysis of the diff or AST, not from a command's
  output.
- **advisory** — human judgment, clearly labeled as such, never presented as fact.

**If a check cannot produce evidence in one of these tiers, it does not ship.** A
reviewer that emits LLM opinions is the thing we are replacing, not the thing we are
building.

## Scope

**In scope:**

- Python repos only.
- Git repos only.
- Operates on a diff.
- Ships as a CLI and an MCP server.

**Out of scope** — do not build any of these unless explicitly asked:

- Web UI
- Database
- Auth
- Config file system
- Plugin architecture
- Support for other languages
- GitHub or CI integration
- Packaging for PyPI

## Architecture (non-negotiable)

Three layers, strictly separated:

1. **`core/`** — check functions. Plain Python. Each takes concrete input (diff, repo
   path, AST) and returns structured findings with evidence. This layer knows nothing
   about how it's invoked.
2. **`cli.py`** — thin adapter. Parses args, calls core, renders a report.
3. **`mcp_server.py`** — thin adapter. Exposes the same core functions as MCP tools
   with schemas.

**No logic is ever duplicated between the two adapters. Both call the same core.**

Import direction is one-way: `cli.py → core` and `mcp_server.py → core`. Neither
adapter imports the other, and `core/` imports neither adapter.

## How we work

- Show the full file tree and wait for approval before creating files.
- Implement one thing per turn, then stop and show it.
- When there's a real choice between two approaches, present both with tradeoffs
  instead of picking silently.
- State assumptions before starting. If something is ambiguous, ask rather than guess.
- Touch only what the current task requires. No drive-by improvements to adjacent code.
- Match the existing style of the project as it develops.
