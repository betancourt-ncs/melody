"""Unified-diff parsing.

Turns `git diff` output into the concrete input the checks consume. This module
does not decide whether anything is wrong; it only describes what changed.

Layer: core. This module knows nothing about how mel is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Hunk:
    """One @@ block of a unified diff.

    `body` holds the raw lines including their leading ' ', '+' or '-' marker,
    so a check can reconstruct both sides of the change without re-reading git.
    """

    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    body: tuple[str, ...]


@dataclass(frozen=True)
class FileDiff:
    """One file's entry in a diff."""

    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...] = ()
    is_new: bool = False
    is_deleted: bool = False
    is_rename: bool = False

    @property
    def path(self) -> str | None:
        """The path to report against: new path when present, else old."""
        return self.new_path or self.old_path


@dataclass(frozen=True)
class ParsedDiff:
    """A whole diff, as the checks receive it."""

    raw: str
    files: tuple[FileDiff, ...] = ()

    @property
    def added_lines(self) -> int:
        """Count of lines the diff adds. Used by size and scope checks."""
        raise NotImplementedError("added_lines is not implemented yet")


def parse_unified_diff(text: str) -> ParsedDiff:
    """Parse unified diff text into files and hunks.

    Args:
        text: Output of `git diff` (or `git show`, or a diff file).

    Returns:
        The parsed diff, with one FileDiff per file entry.

    Raises:
        ReviewError: If `text` is not a parseable unified diff.

    Not implemented in this task. Task 1 ships the skeleton only: no check
    logic and no diff parsing, per the "one thing per turn" working agreement.
    """
    raise NotImplementedError("parse_unified_diff is not implemented yet")
