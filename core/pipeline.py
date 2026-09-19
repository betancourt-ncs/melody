"""The review pipeline: validate input, run checks, assemble a report.

This is the one place both adapters call into. It holds no check logic and no
rendering; it wires the two together.

Layer: core. This module knows nothing about how mel is invoked.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

from core.checks import CHECKS, Check, ReviewContext
from core.models import Finding, Report, ReviewError


def validate_repo(repo_path: Path) -> None:
    """Confirm `repo_path` is a git repo mel can review.

    Args:
        repo_path: Directory to review.

    Raises:
        ReviewError: If the path is not a directory, or is not a git repo.
    """
    if not repo_path.is_dir():
        raise ReviewError(f"not a directory: {repo_path}")
    # `.git` is a directory in a normal clone and a file in a worktree; both count.
    if not (repo_path / ".git").exists():
        raise ReviewError(f"not a git repository: {repo_path}")


def resolve_diff_ref(repo_path: Path, diff_ref: str) -> str:
    """Resolve `diff_ref` to a commit sha in `repo_path`.

    Args:
        repo_path: The repo to resolve against.
        diff_ref: Any revision git understands, e.g. "HEAD~1", "main", a sha.

    Returns:
        The full commit sha.

    Raises:
        ReviewError: If git cannot resolve the ref, or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "--end-of-options", f"{diff_ref}^{{commit}}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ReviewError("git is not installed or not on PATH") from exc

    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise ReviewError(f"cannot resolve diff ref {diff_ref!r} in {repo_path}")
    return sha


def build_context(repo_path: Path, diff_ref: str) -> ReviewContext:
    """Collect everything the checks need for one run.

    Args:
        repo_path: Absolute path to the git repo.
        diff_ref: A ref already confirmed to resolve.

    Returns:
        A context carrying the parsed diff and the repo's tracked file list.

    Raises:
        ReviewError: If the diff cannot be read or parsed.

    Not implemented in this task. Task 1 ships the skeleton only, and the
    pipeline does not reach this function while `CHECKS` is empty.
    """
    raise NotImplementedError("build_context is not implemented yet")


def run_checks(context: ReviewContext, checks: Sequence[Check]) -> list[Finding]:
    """Run every check against the context and collect the findings.

    Args:
        context: The prepared review context.
        checks: Checks to run, in order.

    Returns:
        Findings in check order. A check that raises is not swallowed: a broken
        check is a bug in mel, not a clean result for the user.
    """
    findings: list[Finding] = []
    for check in checks:
        findings.extend(check(context))
    return findings


def review_diff(
    repo_path: Path,
    diff_ref: str,
    checks: Sequence[Check] | None = None,
) -> Report:
    """Review one diff and return a report.

    Args:
        repo_path: Repo to review, e.g. Path.cwd().
        diff_ref: Revision to diff against, e.g. "HEAD~1".
        checks: Checks to run. Defaults to the registered set; pass an explicit
            sequence to run a subset.

    Returns:
        A report whose `exit_code` is 0 when clean and 1 when not.

    Raises:
        ReviewError: If the repo or ref is unusable. That is a usage error, not
            a finding, because no review happened.
    """
    validate_repo(repo_path)
    resolved_ref = resolve_diff_ref(repo_path, diff_ref)

    active = tuple(CHECKS if checks is None else checks)
    if not active:
        # Nothing to run yet. A thin report must say so, or "zero findings"
        # reads as a clean bill of health that this run did not earn.
        return Report(
            diff_ref=diff_ref,
            resolved_ref=resolved_ref,
            repo_path=str(repo_path),
            notes=(
                "No checks are registered in this build, so no findings were produced.",
                "This run did not parse the diff or read any file in the repo.",
            ),
        )

    context = build_context(repo_path, diff_ref)
    findings = run_checks(context, active)
    return Report(
        diff_ref=diff_ref,
        resolved_ref=resolved_ref,
        repo_path=str(repo_path),
        findings=tuple(findings),
        files_reviewed=len(context.diff.files),
    )
