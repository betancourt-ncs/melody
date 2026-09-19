"""Tests for the Surgical Changes orphan-detection check (SC001).

Two cases, per the task's done-when:

- A commit that removes a still-used symbol → at least one PROVEN finding,
  whose evidence includes the command run and its output.
- A commit with no orphans → zero findings.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import cli  # noqa: E402
from core.models import EvidenceTier, Pillar  # noqa: E402


def run_melody(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real `melody` command as a subprocess. Skips if not installed."""
    return subprocess.run(
        [_melody_executable(), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _melody_executable() -> str:
    candidate = Path(sys.executable).parent / "melody"
    if not candidate.exists():
        pytest.skip("the `melody` console script is not installed; run: pip install -e '.[dev]'")
    return str(candidate)


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, ready for a second commit."""
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "first", cwd=tmp_path)
    return tmp_path


# --- Case A: removed symbol still referenced elsewhere -------------------


def test_removed_function_still_referenced_produces_finding(
    git_repo: Path,
) -> None:
    """Commit 2 removes `foo` from a.py while b.py still calls it."""
    (git_repo / "a.py").write_text("def foo():\n    pass\n")
    (git_repo / "b.py").write_text("from a import foo\nfoo()\n")
    git("add", "-A", cwd=git_repo)
    git("commit", "-q", "-m", "add foo and its caller", cwd=git_repo)

    # Commit 3: remove foo from a.py — b.py is now broken.
    (git_repo / "a.py").write_text("# foo removed\n")
    git("add", "-A", cwd=git_repo)
    git("commit", "-q", "-m", "remove foo", cwd=git_repo)

    result = run_melody(
        "review", "--diff", "HEAD~1", "--format", "json", cwd=git_repo
    )

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)

    findings = payload["findings"]
    assert len(findings) >= 1, f"expected >=1 finding, got {findings}"

    orphan_findings = [
        f for f in findings if f["rule_id"] == "SC001_orphaned_symbol"
    ]
    assert orphan_findings, f"expected SC001 finding, got {[f['rule_id'] for f in findings]}"

    ev = orphan_findings[0]["evidence"][0]
    assert ev["tier"] == "proven"
    assert ev["command"] is not None
    assert "grep" in ev["command"]
    assert "foo" in ev["command"]
    assert ev["detail"]  # non-empty
    assert "b.py" in ev["detail"]


# --- Case B: clean diff, no orphans --------------------------------------


def test_clean_commit_produces_zero_findings(git_repo: Path) -> None:
    """Commit 2 changes a.py without removing any used symbols."""
    (git_repo / "a.py").write_text("x = 2\n")
    git("add", "-A", cwd=git_repo)
    git("commit", "-q", "-m", "change x value", cwd=git_repo)

    result = run_melody("review", "--diff", "HEAD~1", "--format", "json", cwd=git_repo)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["findings"] == []
