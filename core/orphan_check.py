"""Surgical Changes — orphan detection.

For each symbol the diff removed or renamed (function, class, import,
module-level variable), determine whether the change left the repo broken or
orphaned:

- **Broken reference (case A):** a removed/renamed symbol still has references
  elsewhere in the repo.
- **Orphaned definition (case B):** the diff removed the last remaining call
  site of a symbol that still exists in the repo.

Evidence tier: **proven**.  Every finding carries the actual ``grep`` command
and its output.  No model judgment anywhere in this check's pipeline.

Layer: core. This module knows nothing about how melody is invoked.
"""
from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from typing import Sequence

from core.checks import Check, ReviewContext
from core.diff import FileDiff
from core.models import Evidence, EvidenceTier, Finding, Location, Pillar

RULE_ID = "SC001_orphaned_symbol"

# --- candidate extraction (text layer) -------------------------------------

_DEF = re.compile(r"^def\s+(\w+)\s*\(")
_ASYNC_DEF = re.compile(r"^async\s+def\s+(\w+)\s*\(")
_CLASS = re.compile(r"^class\s+(\w+)\s*[\(:]")
_IMPORT = re.compile(r"^import\s+(\w+)")
_FROM_IMPORT = re.compile(r"^from\s+\S+\s+import\s+(\w+)")
_MODULE_VAR = re.compile(r"^(\w+)\s*=")

_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    _DEF,
    _ASYNC_DEF,
    _CLASS,
    _IMPORT,
    _FROM_IMPORT,
    _MODULE_VAR,
)


@dataclass(frozen=True)
class RemovedSymbol:
    """A symbol found on a removed diff line.

    Attributes:
        name: The identifier as it appeared in the diff.
        kind: ``function`` / ``class`` / ``import`` / ``variable``.
        file: The file (as a path string relative to repo root) it was in.
        source_line: The removed line text (with the leading ``-`` stripped).
    """

    name: str
    kind: str
    file: str
    source_line: str


def _extract_candidates(file_diff: FileDiff) -> list[RemovedSymbol]:
    """Scan the diff's removed lines for candidate symbols (text layer)."""
    path = file_diff.path
    if path is None:
        return []

    candidates: list[RemovedSymbol] = []
    for raw in file_diff.removed_lines():
        for pat in _SYMBOL_PATTERNS:
            m = pat.match(raw)
            if m:
                name = m.group(1)
                if pat is _DEF or pat is _ASYNC_DEF:
                    kind = "function"
                elif pat is _CLASS:
                    kind = "class"
                elif pat is _IMPORT or pat is _FROM_IMPORT:
                    kind = "import"
                else:
                    kind = "variable"
                candidates.append(
                    RemovedSymbol(name=name, kind=kind, file=path, source_line=raw)
                )
                break
    return candidates


# --- AST confirmation -----------------------------------------------------


def _show_pre_diff_file(repo_path, diff_ref: str, rel_path: str) -> str | None:
    """Return the file content at the *pre-diff* version (``<ref>:<path>``).

    Returns ``None`` when the file did not exist at that revision.
    """
    result = subprocess.run(
        ["git", "show", f"{diff_ref}:{rel_path}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _confirm_top_level(name: str, source: str) -> bool:
    """AST-confirm that *name* is a top-level definition in *source*.

    Returns ``False`` when *source* does not parse or *name* is not a
    top-level ``def``/``class``/``import``/module-level assignment.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[-1]
                if local == name:
                    return True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return True
    return False


# --- repo-wide reference search (grep) ------------------------------------


def _git_grep(name: str, repo_path) -> tuple[str, str]:
    """Search tracked files for *name* via ``git grep``.

    Returns ``(command, output)`` where *command* is the exact string a user
    could paste into a terminal and *output* is the raw stdout.  ``git grep``
    only searches git-tracked files, so ``.git/`` internals and ignored files
    are never hit.
    """
    cmd = f"git grep -n --line-number --word-regexp '{name}'"
    result = subprocess.run(
        ["git", "grep", "-n", "--line-number", "--word-regexp", name],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return cmd, result.stdout


def _filter_external_hits(
    grep_output: str, diff_file: str
) -> list[str]:
    """Keep grep hits that are in *other* files (not the diff's own file)."""
    return [
        line
        for line in grep_output.splitlines()
        if line and not line.startswith(f"{diff_file}:")
    ]


# --- case B: orphaned definitions ----------------------------------------


def _git_grep_excluding(
    name: str, repo_path, exclude_file: str | None = None
) -> tuple[str, str]:
    """``git grep`` for *name*, optionally excluding one file's hits.

    Returns ``(command, output)``.
    """
    cmd = f"git grep -n --line-number --word-regexp '{name}'"
    result = subprocess.run(
        ["git", "grep", "-n", "--line-number", "--word-regexp", name],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    output = result.stdout
    if exclude_file:
        output = "\n".join(
            line
            for line in output.splitlines()
            if line and not line.startswith(f"{exclude_file}:")
        )
    return cmd, output


@dataclass(frozen=True)
class _RepoSymbol:
    """A symbol defined in the repo's current state."""

    name: str
    file: str


def _collect_repo_definitions(repo_path, repo_files) -> list[_RepoSymbol]:
    """AST-scan tracked ``.py`` files for top-level definitions."""
    symbols: list[_RepoSymbol] = []
    for raw_path in repo_files:
        rel = str(raw_path)
        if not rel.endswith(".py"):
            continue
        abs_path = repo_path / rel
        try:
            source = abs_path.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(_RepoSymbol(name=node.name, file=rel))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        symbols.append(_RepoSymbol(name=target.id, file=rel))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[-1]
                    symbols.append(_RepoSymbol(name=local, file=rel))
    return symbols


def _detect_orphaned_definitions(
    context: ReviewContext,
) -> list[Finding]:
    """Case B: the diff removed the last call site of a still-defined symbol.

    For each symbol still defined in the repo, check whether the diff removed
    all references to it.  If the symbol is defined in exactly one file and
    that file's diff removed lines referencing it, but no other file in the
    repo references it, the symbol is orphaned.
    """
    findings: list[Finding] = []

    repo_syms = _collect_repo_definitions(
        context.repo_path, context.repo_root_files
    )

    # Group: name -> set of files where it is defined.
    defs_by_name: dict[str, set[str]] = {}
    for sym in repo_syms:
        defs_by_name.setdefault(sym.name, set()).add(sym.file)

    for file_diff in context.diff.files:
        path = file_diff.path
        if path is None or file_diff.is_new:
            continue

        removed = file_diff.removed_lines()
        # Names *called* on removed lines (call sites, not definitions).
        # A removed `def foo():` is a definition, not a call site; skip it.
        referenced: set[str] = set()
        for line in removed:
            stripped = line.lstrip()
            if (
                stripped.startswith("def ")
                or stripped.startswith("async def ")
                or stripped.startswith("class ")
                or stripped.startswith("import ")
                or stripped.startswith("from ")
            ):
                continue
            for ident in re.findall(r"\b([A-Za-z_]\w*)\s*\(", line):
                referenced.add(ident)

        for name in referenced:
            if name not in defs_by_name:
                continue
            def_files = defs_by_name[name]
            # The symbol must still be defined somewhere in the repo.
            if not def_files:
                continue
            # Exclude the diff file itself when searching for remaining refs.
            cmd, remaining = _git_grep_excluding(
                name, context.repo_path, exclude_file=path
            )
            if remaining.strip():
                continue  # still referenced — not orphaned

            findings.append(
                Finding(
                    rule_id=RULE_ID,
                    pillar=Pillar.SURGICAL_CHANGES,
                    message=(
                        f"symbol '{name}' is still defined in "
                        f"{', '.join(sorted(def_files))} but the diff "
                        f"removed its last remaining reference site"
                    ),
                    location=Location(file=path),
                    evidence=(
                        Evidence(
                            tier=EvidenceTier.PROVEN,
                            summary=(
                                f"grep found no remaining references to "
                                f"'{name}' outside {path}"
                            ),
                            detail=remaining
                            if remaining.strip()
                            else "(no output — zero matches)",
                            command=cmd,
                        ),
                    ),
                )
            )

    return findings


# --- main check function --------------------------------------------------


def check_orphaned_symbols(context: ReviewContext) -> Sequence[Finding]:
    """Detect symbols removed or renamed by the diff that left the repo broken.

    Two cases:

    A. **Broken reference**: a removed/renamed symbol still has references
       elsewhere in the repo.
    B. **Orphaned definition**: the diff removed the last call site of a
       symbol that still exists in the repo.

    Evidence is PROVEN: every finding carries the grep command and its output.
    """
    findings: list[Finding] = []

    # --- Case A: removed symbol still referenced elsewhere ----------------
    for file_diff in context.diff.files:
        if file_diff.is_new:
            continue

        candidates = _extract_candidates(file_diff)
        if not candidates:
            continue

        path = file_diff.path
        pre_diff_source: str | None = None
        if path is not None:
            pre_diff_source = _show_pre_diff_file(
                context.repo_path, context.diff_ref, path
            )

        for cand in candidates:
            confirmed = True
            if pre_diff_source is not None:
                confirmed = _confirm_top_level(cand.name, pre_diff_source)
            if not confirmed:
                continue

            cmd, grep_output = _git_grep(cand.name, context.repo_path)
            external_hits = _filter_external_hits(grep_output, cand.file)

            if not external_hits:
                continue

            findings.append(
                Finding(
                    rule_id=RULE_ID,
                    pillar=Pillar.SURGICAL_CHANGES,
                    message=(
                        f"removed {cand.kind} '{cand.name}' is still "
                        f"referenced in {len(external_hits)} other location(s)"
                    ),
                    location=Location(file=cand.file),
                    evidence=(
                        Evidence(
                            tier=EvidenceTier.PROVEN,
                            summary=(
                                f"grep for '{cand.name}' found "
                                f"{len(external_hits)} reference(s) outside "
                                f"{cand.file}"
                            ),
                            detail="\n".join(external_hits),
                            command=cmd,
                        ),
                    ),
                )
            )

    # --- Case B: orphaned definitions ------------------------------------
    findings.extend(_detect_orphaned_definitions(context))

    return findings
