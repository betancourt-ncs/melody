"""Goal-Driven Execution \u2014 test-reproduction verification.

For a "fix commit" (a diff that adds or modifies at least one test file and
also has hunks in at least one non-test file), determine whether the diff's
new or modified test actually reproduces the bug the diff claims to fix.

The check reconstructs the pre-diff state in a temporary git worktree, grafts
the new test in, and runs ``pytest`` against that worktree:

- pytest exits 0 \u2192 the test passes against the buggy code \u2192 the test does
  NOT reproduce the bug. **PROVEN** finding.
- pytest exits non-zero \u2192 the test reproduces the bug. Zero findings for
  that test \u2014 the test is doing its job.
- pytest exits non-zero because the test cannot be collected against the
  pre-diff code (e.g. it imports symbols the fix commit adds) \u2192 **PROVEN**
  finding with a clear "cannot be evaluated" message.

Evidence tier: **proven**.  Every finding carries the exact ``pytest`` command
and its actual stdout/stderr.

Layer: core. This module knows nothing about how melody is invoked.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.checks import ReviewContext
from core.diff import FileDiff
from core.models import Evidence, EvidenceTier, Finding, Location, Pillar, ReviewError

RULE_ID = "GD001_test_does_not_reproduce_bug"

#: Exit codes that pytest uses. We treat any of these as "the test could not
#: be evaluated against the pre-diff code" rather than "the test failed".
_PYTEST_COLLECTION_ERROR_CODES = {2, 3, 4, 5}


# --- file classification -------------------------------------------------


def _is_test_path(path: str) -> bool:
    """Heuristic: is this path a test file?

    True when any of the following holds:

    - the basename starts with ``test_`` and ends with ``.py``
    - the basename ends with ``_test.py``
    - the path contains a ``/tests/`` segment and the basename is not
      ``__init__.py`` (package markers are never test files)

    The ``/tests/`` segment rule is meant to catch things like
    ``tests/integration/foo.py``; ``tests/__init__.py`` is a package
    marker that pytest never collects.
    """
    basename = os.path.basename(path)
    if basename.startswith("test_") and basename.endswith(".py"):
        return True
    if basename.endswith("_test.py"):
        return True
    if basename == "__init__.py":
        return False
    parts = path.split("/")
    if "tests" in parts:
        return True
    return False


def _partition_diff(diff_files) -> tuple[list[str], list[str]]:
    """Split diff files into (test_paths, code_paths).

    Both lists contain the diff's "new" path (or old path if the file was
    deleted). Non-``.py`` files are ignored \u2014 the check only reviews Python.
    """
    test_paths: list[str] = []
    code_paths: list[str] = []
    for fd in diff_files:
        path = fd.path
        if path is None or not path.endswith(".py"):
            continue
        if _is_test_path(path):
            test_paths.append(path)
        else:
            code_paths.append(path)
    return test_paths, code_paths


# --- git helpers ----------------------------------------------------------


def _git_run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process.

    Raises:
        ReviewError: If git is unavailable.
    """
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ReviewError("git is not installed or not on PATH") from exc


def _resolve_base(repo_path: Path, diff_ref: str) -> str:
    """Return the full sha of ``diff_ref``, the diff's base (pre-diff) commit.

    ``diff_ref`` names the old/buggy state: the pipeline diffs ``diff_ref``
    against the working tree (``git diff --no-color <diff_ref>``), so the
    pre-diff code lives at ``diff_ref`` itself, not its parent. This helper
    resolves that name to a commit sha.

    Raises:
        ReviewError: If the ref cannot be resolved, or git is unavailable.
    """
    result = _git_run(
        ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{diff_ref}^{{commit}}"],
        repo_path,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise ReviewError(
            f"cannot resolve diff ref {diff_ref!r} in {repo_path} "
            "(is the ref invalid?)"
        )
    return sha


def _read_new_file(repo_path: Path, rel_path: str) -> str | None:
    """Return the new-side content of ``rel_path`` from the working tree.

    The diff's new side is the working tree (``git diff <diff_ref>`` diffs
    against it), so the new test lives in the file on disk, not at any git
    revision. Returns ``None`` if the file is absent.
    """
    target = repo_path / rel_path
    try:
        return target.read_text()
    except FileNotFoundError:
        return None


def _worktree_add(source_repo: Path, worktree: Path, commit: str) -> None:
    """Detach a worktree at *worktree* checked out at *commit*.

    Raises:
        ReviewError: If the worktree cannot be created.
    """
    result = _git_run(
        ["git", "worktree", "add", "--detach", str(worktree), commit],
        source_repo,
    )
    if result.returncode != 0:
        raise ReviewError(
            f"git worktree add failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _worktree_remove(source_repo: Path, worktree: Path) -> None:
    """Force-remove *worktree*. Best-effort; never raises."""
    _git_run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        source_repo,
    )


# --- pytest invocation ---------------------------------------------------


@dataclass(frozen=True)
class _PytestResult:
    """The outcome of running pytest on one test file."""

    test_path: str
    returncode: int
    stdout: str
    stderr: str
    command: str

    @property
    def combined_output(self) -> str:
        out = self.stdout
        if self.stderr:
            if out and not out.endswith("\n"):
                out += "\n"
            out += self.stderr
        return out


def _run_pytest(worktree: Path, test_path: str) -> _PytestResult:
    """Run ``pytest -q --no-header <test_path>`` against *worktree*.

    Uses ``sys.executable -m pytest`` so the subprocess resolves pytest
    even when the parent process's ``$PATH`` does not contain it (e.g. when
    the check is invoked from inside an editor or a CI step that only
    activated a virtualenv via ``VIRTUAL_ENV`` rather than by sourcing
    ``activate``).  The command string is the exact one a human could paste.
    """
    pytest_invocation = f"{sys.executable} -m pytest -q --no-header {test_path}"
    cmd_str = f"cd {worktree} && {pytest_invocation}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", test_path],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return _PytestResult(
        test_path=test_path,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        command=cmd_str,
    )


def _looks_like_collection_error(result: _PytestResult) -> bool:
    """True when the pytest failure looks like a collection / import error.

    Pytest distinguishes between "tests ran and some failed" (exit 1) and
    "pytest could not even collect the tests" (exit 2..5, or stdout that
    mentions collection / import problems).
    """
    if result.returncode in _PYTEST_COLLECTION_ERROR_CODES:
        return True
    haystack = (result.stdout + "\n" + result.stderr).lower()
    markers = (
        "error collecting",
        "errors during collection",
        "collected 0 items",
        "importerror",
        "modulenotfounderror",
        "failed to import",
        "syntaxerror",
        "indentationerror",
    )
    return any(m in haystack for m in markers)


# --- check ---------------------------------------------------------------


def check_test_verification(context: ReviewContext) -> Sequence[Finding]:
    """Verify whether the diff's added/modified tests reproduce the bug.

    A "fix commit" is a diff that adds or modifies at least one test file
    AND has hunks in at least one non-test file. For each new or modified
    test file in such a diff, the check:

    1. Resolves the diff's base commit (``diff_ref`` -- the pre-diff code).
    2. Creates a detached worktree at that base state.
    3. Grafts the diff's new version of the test file into the worktree.
    4. Runs ``pytest -q --no-header`` against that test file in the worktree.

    Results:

    - pytest exits 0 \u2192 test passes against buggy code \u2192 PROVEN finding,
      ``GD001_test_does_not_reproduce_bug``.
    - pytest exits non-zero because the test failed \u2192 no finding; the
      test reproduces the bug.
    - pytest exits non-zero because the test could not be collected \u2192
      PROVEN finding with a "cannot be evaluated" message.

    Non-fix commits (no test file changes, or no code changes) emit zero
    findings.
    """
    test_paths, code_paths = _partition_diff(context.diff.files)
    if not test_paths or not code_paths:
        return []

    base_sha = _resolve_base(context.repo_path, context.diff_ref)

    worktree = Path(tempfile.mkdtemp(prefix="melody-testverify-"))
    findings: list[Finding] = []
    try:
        _worktree_add(context.repo_path, worktree, base_sha)

        for test_path in test_paths:
            new_content = _read_new_file(context.repo_path, test_path)
            if new_content is None:
                # The new version of the test file is not in the working tree.
                # Defensive: should not happen for added/modified files.
                continue

            target = worktree / test_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content)

            result = _run_pytest(worktree, test_path)

            if result.returncode == 0:
                findings.append(
                    Finding(
                        rule_id=RULE_ID,
                        pillar=Pillar.GOAL_DRIVEN_EXECUTION,
                        message=(
                            f"test {test_path} passes against pre-diff code; "
                            "it does not reproduce the bug the diff claims to fix"
                        ),
                        location=Location(file=test_path),
                        evidence=(
                            Evidence(
                                tier=EvidenceTier.PROVEN,
                                summary=(
                                    f"pytest exited 0 against {base_sha[:10]}; "
                                    "the test cannot fail without the fix"
                                ),
                                detail=result.combined_output.strip() or "(no output)",
                                command=result.command,
                            ),
                        ),
                    )
                )
            elif _looks_like_collection_error(result):
                findings.append(
                    Finding(
                        rule_id=RULE_ID,
                        pillar=Pillar.GOAL_DRIVEN_EXECUTION,
                        message=(
                            f"test {test_path} cannot be evaluated against "
                            "pre-diff code \u2014 does not establish a goal "
                            "(the diff is the only thing that makes it collect)"
                        ),
                        location=Location(file=test_path),
                        evidence=(
                            Evidence(
                                tier=EvidenceTier.PROVEN,
                                summary=(
                                    f"pytest exited {result.returncode} and "
                                    "reported a collection/import error against "
                                    f"{base_sha[:10]}"
                                ),
                                detail=result.combined_output.strip() or "(no output)",
                                command=result.command,
                            ),
                        ),
                    )
                )
            # else: test failed against pre-diff code \u2014 it does reproduce
            # the bug. Zero findings for this test path.
    finally:
        _worktree_remove(context.repo_path, worktree)

    return findings
