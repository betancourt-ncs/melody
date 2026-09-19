"""Smoke tests for the Task 1 skeleton: the tool runs and reports honestly.

No check logic exists yet, so these tests cover the adapter contract, not
findings. They assert the two things Task 1 must get right: a real review runs
and prints a zero-finding report, and a bad invocation fails as a usage error
rather than crashing or reporting a clean bill of health.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import cli  # noqa: E402
from core.models import Evidence, EvidenceTier, Finding, Pillar  # noqa: E402


def run_mel(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the real `mel` command as a subprocess. Skips if it is not installed."""
    return subprocess.run(
        [_mel_executable(), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _mel_executable() -> str:
    candidate = Path(sys.executable).parent / "mel"
    if not candidate.exists():
        pytest.skip("the `mel` console script is not installed; run: pip install -e '.[dev]'")
    return str(candidate)


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with two commits, so HEAD~1 resolves."""
    git("init", "-q", "-b", "main", cwd=tmp_path)
    git("config", "user.email", "test@example.com", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)

    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "first", cwd=tmp_path)

    (tmp_path / "a.py").write_text("x = 2\n")
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "second", cwd=tmp_path)
    return tmp_path


def test_review_reports_zero_findings_without_crashing(git_repo: Path) -> None:
    result = run_mel("review", "--diff", "HEAD~1", cwd=git_repo)

    assert result.returncode == 0, result.stderr
    assert "0 findings." in result.stdout
    assert "Nothing was found, which is not the same as nothing being wrong." in result.stdout


def test_report_states_that_no_checks_ran(git_repo: Path) -> None:
    result = run_mel("review", "--diff", "HEAD~1", cwd=git_repo)

    assert "No checks are registered in this build" in result.stdout


def test_unresolvable_ref_is_a_usage_error(git_repo: Path) -> None:
    result = run_mel("review", "--diff", "not-a-real-ref", cwd=git_repo)

    assert result.returncode == 2
    assert "cannot resolve diff ref" in result.stderr
    assert "Traceback" not in result.stderr


def test_non_git_directory_is_a_usage_error(tmp_path: Path) -> None:
    result = run_mel("review", "--diff", "HEAD~1", cwd=tmp_path)

    assert result.returncode == 2
    assert "not a git repository" in result.stderr
    assert "Traceback" not in result.stderr


def test_json_format_reports_the_same_empty_result(git_repo: Path) -> None:
    result = run_mel("review", "--diff", "HEAD~1", "--format", "json", cwd=git_repo)

    assert result.returncode == 0, result.stderr
    payload = __import__("json").loads(result.stdout)
    assert payload["findings"] == []
    assert payload["exit_code"] == 0
    assert payload["resolved_ref"]


def test_finding_without_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="no evidence"):
        Finding(rule_id="x", pillar=Pillar.SIMPLICITY_FIRST, message="dead code")


def test_proven_evidence_must_carry_its_command() -> None:
    with pytest.raises(ValueError, match="must record the command"):
        Evidence(tier=EvidenceTier.PROVEN, summary="zero call sites")


def test_inferred_evidence_must_not_claim_a_command() -> None:
    with pytest.raises(ValueError, match="must not record a command"):
        Evidence(tier=EvidenceTier.INFERRED, summary="unused import", command="grep -r x")


def test_cli_main_returns_zero_directly(git_repo: Path) -> None:
    """The adapter is callable in-process, not only as a console script."""
    assert cli.main(["review", "--diff", "HEAD~1", "--repo", str(git_repo)]) == 0
