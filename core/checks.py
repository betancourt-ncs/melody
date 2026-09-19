"""Check functions and the registry they live in.

A check is a plain function: concrete input in, findings with evidence out. It
receives a `ReviewContext` and returns zero or more `Finding`s. A check that
cannot attach evidence to a claim must not make the claim.

Layer: core. This module knows nothing about how melody is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from core.diff import ParsedDiff
from core.models import Finding


@dataclass(frozen=True)
class ReviewContext:
    """Everything a check is allowed to touch.

    A check gets the repo it is reviewing, the parsed diff, and nothing else.
    In particular it does not get a way to reach the network or the adapter
    that invoked it.

    Attributes:
        repo_path: Absolute path to the git repo being reviewed.
        diff_ref: The revision string the caller asked to diff against.
        diff: The parsed diff under review.
        repo_root_files: Paths tracked by git at the reviewed commit. Checks
            that look for call sites outside the diff need this to know what
            "the rest of the repo" means.
    """

    repo_path: Path
    diff_ref: str
    diff: ParsedDiff
    repo_root_files: tuple[Path, ...] = ()


#: A check: context in, findings out. No side effects, no I/O beyond reading
#: the repo it was handed.
Check = Callable[[ReviewContext], Sequence[Finding]]


#: The registry the pipeline runs. Each entry serves exactly one pillar from
#: PROJECT.md and attaches evidence in a tier the model accepts.
from core.orphan_check import check_orphaned_symbols  # noqa: E402


from core.test_verification_check import check_test_verification  # noqa: E402

CHECKS: tuple[Check, ...] = (
    check_orphaned_symbols,
    check_test_verification,
)
