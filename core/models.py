"""Data model for findings and the evidence that justifies them.

This module is the enforcement point for the project's evidence rule: a
`Finding` cannot be constructed without evidence, and `PROVEN` evidence cannot
be constructed without the command that produced it. See PROJECT.md.

Layer: core. This module knows nothing about how mel is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReviewError(Exception):
    """Raised when mel cannot review at all (bad repo, bad ref, unreadable diff).

    This is distinct from a finding: it means the review did not happen, so
    there is nothing to report and no evidence to attach.
    """


class EvidenceTier(str, Enum):
    """How a finding's evidence was produced. Strongest first.

    A finding is only as strong as its weakest evidence.
    """

    #: A command ran and produced this output.
    PROVEN = "proven"
    #: Derived from static analysis of the diff or AST, not a command's output.
    INFERRED = "inferred"
    #: Human judgment. Always labeled as such, never presented as fact.
    ADVISORY = "advisory"


#: Lower rank is stronger. Used to pick a finding's overall tier.
_TIER_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.PROVEN: 0,
    EvidenceTier.INFERRED: 1,
    EvidenceTier.ADVISORY: 2,
}


class Pillar(str, Enum):
    """The four pillars from PROJECT.md. Every finding names exactly one."""

    THINK_BEFORE_CODING = "think_before_coding"
    SIMPLICITY_FIRST = "simplicity_first"
    SURGICAL_CHANGES = "surgical_changes"
    GOAL_DRIVEN_EXECUTION = "goal_driven_execution"


@dataclass(frozen=True)
class Evidence:
    """One piece of justification for a finding.

    Attributes:
        tier: How this evidence was produced.
        summary: One line stating what this evidence shows.
        detail: The raw material -- command output, AST detail, or reasoning.
        command: The exact command that produced `detail`. Required for
            `PROVEN` and forbidden otherwise: a "proven" claim with no command
            behind it is the failure mode this project exists to prevent.
    """

    tier: EvidenceTier
    summary: str
    detail: str = ""
    command: str | None = None

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("evidence requires a summary; unlabeled evidence is an opinion")
        if self.tier is EvidenceTier.PROVEN and not self.command:
            raise ValueError("PROVEN evidence must record the command that produced it")
        if self.tier is not EvidenceTier.PROVEN and self.command is not None:
            raise ValueError(
                f"{self.tier.value} evidence must not record a command; "
                "only PROVEN evidence comes from running one"
            )

    def as_dict(self) -> dict[str, Any]:
        """Plain-data form. Lives in core so both adapters serialize identically."""
        payload: dict[str, Any] = {
            "tier": self.tier.value,
            "summary": self.summary,
            "detail": self.detail,
        }
        if self.command is not None:
            payload["command"] = self.command
        return payload


@dataclass(frozen=True)
class Location:
    """Where a finding applies.

    `line` is None for findings about a whole file, and None on both fields
    means the finding is about the diff or repo as a whole.
    """

    file: str | None = None
    line: int | None = None

    def __str__(self) -> str:
        if self.file is None:
            return "<repo>"
        if self.line is None:
            return self.file
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class Finding:
    """One reported pathology, carried by the evidence that justifies it.

    Construction fails if there is no evidence. That is deliberate: a finding
    this tool cannot justify is a finding this tool does not emit.
    """

    rule_id: str
    pillar: Pillar
    message: str
    location: Location = field(default_factory=Location)
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        evidence = tuple(self.evidence)
        if not evidence:
            raise ValueError(
                f"finding {self.rule_id!r} has no evidence; "
                "checks that cannot produce evidence do not ship (see PROJECT.md)"
            )
        object.__setattr__(self, "evidence", evidence)
        if not self.rule_id.strip():
            raise ValueError("finding requires a rule_id")

    @property
    def tier(self) -> EvidenceTier:
        """The finding's overall tier: the weakest tier among its evidence."""
        return min((e.tier for e in self.evidence), key=lambda t: _TIER_RANK[t])

    def as_dict(self) -> dict[str, Any]:
        """Plain-data form. Lives in core so both adapters serialize identically."""
        return {
            "rule_id": self.rule_id,
            "pillar": self.pillar.value,
            "message": self.message,
            "location": {"file": self.location.file, "line": self.location.line},
            "tier": self.tier.value,
            "evidence": [e.as_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class Report:
    """The result of one review run.

    Attributes:
        diff_ref: The revision string the review was asked to diff against.
        resolved_ref: The commit sha `diff_ref` resolved to, when it resolved.
        repo_path: Absolute path of the repo that was reviewed.
        findings: Everything the checks produced, in check order.
        files_reviewed: How many files the diff touched.
        notes: Statements about what this run did and did not do. Rendered so a
            thin report is never mistaken for a clean bill of health.
    """

    diff_ref: str
    resolved_ref: str | None = None
    repo_path: str = ""
    findings: tuple[Finding, ...] = ()
    files_reviewed: int = 0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))
        object.__setattr__(self, "notes", tuple(self.notes))

    @property
    def exit_code(self) -> int:
        """0 when clean, 1 when there is something to act on."""
        return 1 if self.findings else 0

    def findings_for(self, pillar: Pillar) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.pillar is pillar)

    def as_dict(self) -> dict[str, Any]:
        """Plain-data form. Lives in core so both adapters serialize identically."""
        return {
            "diff_ref": self.diff_ref,
            "resolved_ref": self.resolved_ref,
            "repo_path": self.repo_path,
            "files_reviewed": self.files_reviewed,
            "exit_code": self.exit_code,
            "findings": [f.as_dict() for f in self.findings],
            "notes": list(self.notes),
        }
