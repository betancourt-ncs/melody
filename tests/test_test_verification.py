"""Tests for the Goal-Driven Execution test-verification check (GD001).

Two cases are required by the brief:

- A fix commit whose new test passes against the buggy parent code -> at
  least one PROVEN ``GD001`` finding whose evidence records the pytest
  command and its output.
- A fix commit whose new test fails against the buggy parent code -> zero
  findings.

Both cases construct a throwaway git repo in-test, build the diff the
check expects, and call the check directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.checks import ReviewContext  # noqa: E402
from core.diff import parse_unified_diff  # noqa: E402
from core.models import EvidenceTier  # noqa: E402
from core.test_verification_check import check_test_verification  # noqa: E402


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def throwaway_repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one initial commit, ready for a fix commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)
    return repo


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    """Write *files* into *repo*, add, commit, and return the new HEAD sha."""
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _build_context(repo: Path, fix_sha: str) -> ReviewContext:
    """Build a ReviewContext the check can consume for the fix commit."""
    diff_text = subprocess.check_output(
        ["git", "diff", "--no-color", f"{fix_sha}~1", fix_sha], cwd=repo, text=True
    )
    parsed = parse_unified_diff(diff_text)
    ls = subprocess.check_output(["git", "ls-files"], cwd=repo, text=True).splitlines()
    return ReviewContext(
        repo_path=repo,
        diff_ref=fix_sha,
        diff=parsed,
        repo_root_files=tuple(repo / p for p in ls if p),
    )


# --- Case A: pathology -- new test passes against buggy code -------------


def test_fix_test_passes_against_buggy_code_produces_finding(
    throwaway_repo: Path,
) -> None:
    """The new test asserts only that the buggy function is callable.

    That assertion is true both before and after the fix, so the test does
    not reproduce the bug. The check must emit a GD001 PROVEN finding.
    """
    _commit(
        throwaway_repo,
        {"app/__init__.py": "", "app/calc.py": "def add(a, b):\n    return a * b\n"},
        "initial: buggy add()",
    )
    fix_sha = _commit(
        throwaway_repo,
        {
            "app/calc.py": "def add(a, b):\n    return a + b\n",
            "tests/__init__.py": "",
            "tests/test_calc.py": (
                "from app.calc import add\n\n\n"
                "def test_add_is_callable():\n"
                "    assert callable(add)\n"
            ),
        },
        "fix: add a + b; add a trivial test",
    )
    ctx = _build_context(throwaway_repo, fix_sha)
    findings = check_test_verification(ctx)
    matching = [f for f in findings if f.rule_id == "GD001_test_does_not_reproduce_bug"]
    assert matching, (
        f"expected at least one GD001 finding, got "
        f"{[f.rule_id for f in findings]}"
    )
    f = matching[0]
    assert f.pillar.value == "goal_driven_execution"
    assert f.tier is EvidenceTier.PROVEN
    assert f.evidence, "PROVEN finding must have evidence"
    ev = f.evidence[0]
    assert ev.command is not None
    assert "pytest" in ev.command
    assert ev.detail.strip(), "evidence.detail must be non-empty"


# --- Case B: clean -- new test fails against buggy code ------------------


def test_good_fix_test_fails_against_buggy_code_produces_no_findings(
    throwaway_repo: Path,
) -> None:
    """The new test asserts add(2, 3) == 5.

    Against the buggy ``a * b`` parent, ``add(2, 3)`` returns 6, so pytest
    fails -- the test reproduces the bug. The check emits zero findings.
    """
    _commit(
        throwaway_repo,
        {"app/__init__.py": "", "app/calc.py": "def add(a, b):\n    return a * b\n"},
        "initial: buggy add()",
    )
    fix_sha = _commit(
        throwaway_repo,
        {
            "app/calc.py": "def add(a, b):\n    return a + b\n",
            "tests/__init__.py": "",
            "tests/test_calc.py": (
                "from app.calc import add\n\n\n"
                "def test_add():\n"
                "    assert add(2, 3) == 5\n"
            ),
        },
        "fix: add a + b; add a real test",
    )
    ctx = _build_context(throwaway_repo, fix_sha)
    findings = check_test_verification(ctx)
    assert findings == [], (
        f"expected zero findings (test reproduces the bug), got "
        f"{[(f.rule_id, f.message) for f in findings]}"
    )


# --- Extra: non-fix commits produce zero findings ------------------------


def test_non_fix_commit_produces_zero_findings(throwaway_repo: Path) -> None:
    """A commit that changes only code (no test file) is not a fix commit."""
    _commit(
        throwaway_repo,
        {"app/__init__.py": "", "app/calc.py": "x = 1\n"},
        "initial",
    )
    fix_sha = _commit(throwaway_repo, {"app/calc.py": "x = 2\n"}, "change x value, no test")
    ctx = _build_context(throwaway_repo, fix_sha)
    assert check_test_verification(ctx) == []


def test_pure_test_change_produces_zero_findings(throwaway_repo: Path) -> None:
    """A commit that touches only tests is not a fix commit."""
    _commit(
        throwaway_repo,
        {
            "app/__init__.py": "",
            "app/calc.py": "def add(a, b):\n    return a + b\n",
            "tests/__init__.py": "",
            "tests/test_calc.py": "def test_old():\n    assert True\n",
        },
        "initial: working code + stale test",
    )
    fix_sha = _commit(
        throwaway_repo,
        {"tests/test_calc.py": "def test_new():\n    assert True\n"},
        "rename a test (no code change)",
    )
    ctx = _build_context(throwaway_repo, fix_sha)
    assert check_test_verification(ctx) == []
