"""Unified-diff parsing.

Turns `git diff` output into the concrete input the checks consume. This module
does not decide whether anything is wrong; it only describes what changed.

Layer: core. This module knows nothing about how melody is invoked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.models import ReviewError

# --- diff header patterns -------------------------------------------------

_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_OLD_PATH = re.compile(r"^--- (?:a/)?(.+)$")
_NEW_PATH = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@"
)
_RENAME = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")
_NEW_FILE = re.compile(r"^new file mode")
_DELETED_FILE = re.compile(r"^deleted file mode")


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

    def removed_lines(self) -> tuple[str, ...]:
        """Lines the diff removed, with the leading '-' stripped."""
        return tuple(
            line[1:]
            for hunk in self.hunks
            for line in hunk.body
            if line.startswith("-") and not line.startswith("---")
        )

    def added_text_lines(self) -> tuple[str, ...]:
        """Lines the diff added, with the leading '+' stripped."""
        return tuple(
            line[1:]
            for hunk in self.hunks
            for line in hunk.body
            if line.startswith("+") and not line.startswith("+++")
        )


@dataclass(frozen=True)
class ParsedDiff:
    """A whole diff, as the checks receive it."""

    raw: str
    files: tuple[FileDiff, ...] = ()

    @property
    def added_lines(self) -> int:
        """Count of lines the diff adds. Used by size and scope checks."""
        return sum(
            1
            for f in self.files
            for hunk in f.hunks
            for line in hunk.body
            if line.startswith("+") and not line.startswith("+++")
        )

    @property
    def removed_line_count(self) -> int:
        """Count of lines the diff removes."""
        return sum(
            1
            for f in self.files
            for hunk in f.hunks
            for line in hunk.body
            if line.startswith("-") and not line.startswith("---")
        )


def parse_unified_diff(text: str) -> ParsedDiff:
    """Parse unified diff text into files and hunks.

    Args:
        text: Output of ``git diff`` (or ``git show``, or a diff file).

    Returns:
        The parsed diff, with one :class:`FileDiff` per file entry.

    Raises:
        ReviewError: If *text* is not a parseable unified diff.
    """
    if not text or not text.strip():
        return ParsedDiff(raw=text)

    lines = text.splitlines()
    files: list[FileDiff] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # --- file header ------------------------------------------------
        m = _DIFF_HEADER.match(line)
        if not m:
            i += 1
            continue

        old_path: str | None = m.group(1)
        new_path: str | None = m.group(2)
        is_new = False
        is_deleted = False
        is_rename = False
        hunks: list[Hunk] = []

        # Consume metadata lines until we hit ---/+++ or @@ or the next diff.
        i += 1
        while i < n:
            meta = lines[i]

            if _DIFF_HEADER.match(meta):
                break

            if _NEW_FILE.match(meta):
                is_new = True
                i += 1
                continue

            if _DELETED_FILE.match(meta):
                is_deleted = True
                i += 1
                continue

            rm = _RENAME.match(meta)
            if rm:
                is_rename = True
                old_path = rm.group(1)
                i += 1
                continue

            rmt = _RENAME_TO.match(meta)
            if rmt:
                is_rename = True
                new_path = rmt.group(1)
                i += 1
                continue

            om = _OLD_PATH.match(meta)
            if om:
                old_path = om.group(1)
                i += 1
                continue

            nm = _NEW_PATH.match(meta)
            if nm:
                new_path = nm.group(1)
                i += 1
                continue

            hm = _HUNK_HEADER.match(meta)
            if hm:
                break

            # Other metadata (index, similarity, mode, etc.)
            i += 1

        # --- hunks ------------------------------------------------------
        while i < n:
            hm = _HUNK_HEADER.match(lines[i])
            if not hm:
                break

            old_start = int(hm.group("old_start"))
            old_lines = int(hm.group("old_lines") or 1)
            new_start = int(hm.group("new_start"))
            new_lines = int(hm.group("new_lines") or 1)

            i += 1
            body: list[str] = []
            while i < n and (lines[i].startswith(" ") or lines[i].startswith("+") or lines[i].startswith("-")):
                body.append(lines[i])
                i += 1

            hunks.append(
                Hunk(
                    old_start=old_start,
                    old_lines=old_lines,
                    new_start=new_start,
                    new_lines=new_lines,
                    body=tuple(body),
                )
            )

        files.append(
            FileDiff(
                old_path=old_path,
                new_path=new_path,
                hunks=tuple(hunks),
                is_new=is_new,
                is_deleted=is_deleted,
                is_rename=is_rename,
            )
        )

    if not files and text.strip():
        raise ReviewError("diff text contained no parseable file entries")

    return ParsedDiff(raw=text, files=tuple(files))
