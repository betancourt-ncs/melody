"""CLI adapter for melody.

Thin by design: this module parses arguments, calls `core.pipeline`, and
renders the report. It holds no check logic and no analysis. Anything that
decides whether code is wrong belongs in core/, so the MCP adapter can reuse it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.models import Report, ReviewError
from core.pipeline import review_diff

EXIT_FINDINGS = 1
EXIT_USAGE = 2

_TIER_LABEL = {
    "proven": "PROVEN",
    "inferred": "INFERRED",
    "advisory": "ADVISORY",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser for the `melody` command.
    """
    parser = argparse.ArgumentParser(
        prog="melody",
        description=(
            "Review a diff of AI-generated code. Every finding carries evidence; "
            "melody does not report opinions."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review = subparsers.add_parser(
        "review",
        help="review a diff and report findings",
        description="Review a diff and report findings with their evidence.",
    )
    review.add_argument(
        "--diff",
        required=True,
        metavar="REF",
        help="revision to diff against, e.g. HEAD~1 or main",
    )
    review.add_argument(
        "--repo",
        default=".",
        metavar="PATH",
        help="path to the git repo to review (default: current directory)",
    )
    review.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text)",
    )
    return parser


def render_text(report: Report) -> str:
    """Render a report for a human reading a terminal.

    Args:
        report: The report to render.

    Returns:
        The report as plain text, ending in exactly one newline.
    """
    lines: list[str] = []
    ref = report.resolved_ref[:10] if report.resolved_ref else "?"
    lines.append(f"melody review --diff {report.diff_ref}  ({ref})")
    lines.append("")

    for finding in report.findings:
        lines.append(
            f"[{_TIER_LABEL[finding.tier.value]}] {finding.rule_id} "
            f"({finding.pillar.value}) -- {finding.location}"
        )
        lines.append(f"  {finding.message}")
        for evidence in finding.evidence:
            lines.append(f"  evidence ({evidence.tier.value}): {evidence.summary}")
            if evidence.command:
                lines.append(f"    $ {evidence.command}")
            for detail_line in evidence.detail.splitlines():
                lines.append(f"    {detail_line}")
        lines.append("")

    count = len(report.findings)
    lines.append(f"{count} finding{'s' if count != 1 else ''}.")

    if not report.findings:
        lines.append("")
        lines.append("Nothing was found, which is not the same as nothing being wrong.")
        for note in report.notes:
            lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `melody` command.

    Args:
        argv: Argument list, defaulting to sys.argv[1:].

    Returns:
        0 when the review is clean, 1 when there are findings, 2 on usage error.
    """
    args = build_parser().parse_args(argv)

    try:
        report = review_diff(repo_path=Path(args.repo).resolve(), diff_ref=args.diff)
    except ReviewError as exc:
        print(f"melody: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NotImplementedError as exc:
        # Skeleton stage: a code path exists but carries no implementation yet.
        print(f"melody: not implemented yet: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2))
    else:
        sys.stdout.write(render_text(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
