"""Throwaway fixture for the Goal-Driven Execution test-verification check.

The fixture is NOT checked in. It is built on demand by ``make_sample_repo``,
which creates a small three-commit git repository under ``parent`` and returns
its path. Tests use the returned repo's HEAD to exercise three scenarios:

    HEAD~2 — base
        ``mymod.py`` has the bug ``return a * b`` (should be ``a + b``).
        ``test_mymod.py`` contains ``test_smoke`` (``assert add(0, 0) == 0``),
        which passes against both the buggy and the correct implementation
        (since ``0 * 0 == 0 + 0``), so the test suite is green but the bug is
        present and undetected.

    HEAD~1 — good fix
        ``mymod.py`` is corrected to ``return a + b`` and a new test
        ``test_add_correct`` (``assert add(2, 3) == 5``) is added. This test
        would FAIL against HEAD~2 (where ``2 * 3 == 6 != 5``), so it genuinely
        reproduces the bug.

    HEAD — bad fix (pathology)
        ``mymod.py`` is reverted to ``return a * b`` and a new test
        ``test_trivial_passes`` (``assert callable(add)``) is added. This test
        passes against HEAD~2 (the bug is still present and ``callable(add)``
        is true regardless), so the bug is still undetected even though a
        test was added.

The commit SHAs differ on each run, so callers reference the scenarios by the
relative refs above, not by hardcoded SHAs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["make_sample_repo"]

_TEST_AUTHOR_EMAIL = "test@example.com"
_TEST_AUTHOR_NAME = "Test"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` in ``cwd``; raise ``CalledProcessError`` with stderr on failure."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n"
            f"cwd: {cwd}\n"
            f"exit: {result.returncode}\n"
            f"stderr: {result.stderr}\n"
            f"stdout: {result.stdout}"
        )
    return result


def make_sample_repo(parent: Path) -> Path:
    """Create a three-commit fixture repo at ``parent / "sample_fix_repo"``.

    Returns the path to the new repo. The repo's git history (newest first):

        HEAD    — revert fix, add trivial test   (pathology: tests pass, bug remains)
        HEAD~1  — fix add and add a real test    (correct: test fails against HEAD~2)
        HEAD~2  — base: buggy add                (buggy code + a self-fulfilling test)

    Files are written at the repo root (``mymod.py`` and ``test_mymod.py``) so
    paths stay simple for downstream tests.
    """
    repo = parent / "sample_fix_repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", _TEST_AUTHOR_EMAIL], cwd=repo)
    _run(["git", "config", "user.name", _TEST_AUTHOR_NAME], cwd=repo)

    # Commit 1 (HEAD~2): buggy code + a trivial smoke test that passes against
    # both the buggy and the correct implementation (e.g. add(0, 0) == 0 for both
    # 0*0 and 0+0). Keeps the test file non-empty without locking in the bug.
    (repo / "mymod.py").write_text("def add(a, b):\n    return a * b\n")
    (repo / "test_mymod.py").write_text(
        "from mymod import add\n"
        "\n"
        "def test_smoke():\n"
        "    assert add(0, 0) == 0\n"
    )
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "base: buggy add"], cwd=repo)

    # Commit 2 (HEAD~1): fix the bug and add a test that would fail against HEAD~2.
    (repo / "mymod.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_mymod.py").write_text(
        "from mymod import add\n"
        "\n"
        "def test_smoke():\n"
        "    assert add(0, 0) == 0\n"
        "\n"
        "def test_add_correct():\n"
        "    assert add(2, 3) == 5\n"
    )
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "fix add and add a real test"], cwd=repo)

    # Commit 3 (HEAD): revert the fix and add a trivial test that passes regardless.
    (repo / "mymod.py").write_text("def add(a, b):\n    return a * b\n")
    (repo / "test_mymod.py").write_text(
        "from mymod import add\n"
        "\n"
        "def test_smoke():\n"
        "    assert add(0, 0) == 0\n"
        "\n"
        "def test_trivial_passes():\n"
        "    assert callable(add)\n"
    )
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "revert fix, add trivial test"], cwd=repo)

    # Pin pytest's rootdir to this repo so ``pytest test_mymod.py`` reliably
    # collects every test in the file. Without an in-repo config or conftest,
    # pytest walks up looking for one and can find an unrelated parent config,
    # which silently drops tests when the file is given as a positional arg.
    #
    # NOTE on caching: ``test_mymod.py`` does ``from mymod import add``, which
    # caches the imported module in ``__pycache__``. If a single ``tmp_path``
    # repo is reused across scenarios (e.g. ``reset --hard`` between tests
    # in the same session), pytest may import a stale ``mymod`` from cache.
    # Callers should clear ``__pycache__`` and ``.pytest_cache`` between
    # scenarios -- either via ``pytest --cache-clear`` or a filesystem wipe.
    (repo / "conftest.py").write_text("# Empty: marks this directory as pytest's rootdir.\n")
    (repo / "pytest.ini").write_text("[pytest]\n")

    return repo
