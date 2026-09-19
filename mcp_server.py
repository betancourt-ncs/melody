"""MCP adapter for melody.

Thin by design: each tool validates its arguments and calls the same core
functions the CLI calls. It holds no check logic and no rendering -- `core`
returns the findings, this module only wraps them in an MCP schema.

The MCP SDK is an optional dependency (`pip install melody[mcp]`). It is
imported lazily inside the entry point so that this module stays importable,
and the CLI keeps working, when the SDK is not installed.
"""
from __future__ import annotations

from typing import Any

TOOL_NAME = "review_diff"

TOOL_DESCRIPTION = (
    "Review a diff of AI-generated code in a local git repository and return "
    "findings. Every finding carries evidence tagged proven, inferred or "
    "advisory; melody does not report opinions. Findings are about the diff, not "
    "a judgment of the whole repo."
)

TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "repo_path": {
            "type": "string",
            "description": "Absolute path to the git repository to review.",
        },
        "diff_ref": {
            "type": "string",
            "description": "Revision to diff against, e.g. 'HEAD~1' or 'main'.",
        },
    },
    "required": ["repo_path", "diff_ref"],
}

TOOL_NAME_ASSUMPTIONS = "check_assumptions"
TOOL_DESCRIPTION_ASSUMPTIONS = (
    "Detect assumption gaps and enforce structured reasoning before writing code. "
    "Compares what the planned approach commits to against what the task "
    "description actually specifies. Runs mid-task, before any code exists. "
    "Produces findings in the THINK_BEFORE_CODING pillar."
)
TOOL_SCHEMA_ASSUMPTIONS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_description": {
            "type": "string",
            "description": (
                "The task as given — an agent prompt, issue, or instruction. "
                "What the caller was asked to do."
            ),
        },
        "planned_approach": {
            "type": "string",
            "description": (
                "The plan or approach the agent wrote. Any concrete commitment "
                "(format, library, data structure, error behavior) not grounded "
                "in the task is an assumption gap. If gaps exist, at least two "
                "named approaches with stated tradeoffs are required."
            ),
        },
    },
    "required": ["task_description", "planned_approach"],
}


def review_diff_tool(repo_path: str, diff_ref: str) -> dict[str, Any]:
    """Review a diff and return findings with their evidence.

    Args:
        repo_path: Absolute path to the git repository to review.
        diff_ref: Revision to diff against, e.g. "HEAD~1" or "main".

    Returns:
        The report as plain data. On a usage error, a dict with `ok` False and
        an `error` string, rather than an exception: a bad ref is not a finding
        and must not be mistaken for a clean review.
    """
    from pathlib import Path

    from core.models import ReviewError
    from core.pipeline import review_diff

    try:
        report = review_diff(repo_path=Path(repo_path), diff_ref=diff_ref)
    except ReviewError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **report.as_dict()}


def check_assumptions_tool(
    task_description: str, planned_approach: str
) -> dict[str, Any]:
    """Detect assumption gaps and enforce structured reasoning.

    Args:
        task_description: The task as given — agent prompt, issue, or instruction.
        planned_approach: The plan or approach the agent wrote.

    Returns:
        A dict with findings in the THINK_BEFORE_CODING pillar. Never raises.
    """
    from core.think_before_coding import check_assumptions

    findings = check_assumptions(
        task_description=task_description,
        planned_approach=planned_approach,
    )
    return {
        "ok": True,
        "findings": [
            {
                "rule_id": f.rule_id,
                "pillar": f.pillar.value,
                "message": f.message,
                "tier": f.tier.value,
                "evidence": [e.as_dict() for e in f.evidence],
            }
            for f in findings
        ],
        "exit_code": 1 if findings else 0,
    }


def build_server() -> Any:
    """Build the MCP server exposing melody's core functions as tools.

    Returns:
        A configured MCP server object.

    Raises:
        RuntimeError: If the optional `mcp` dependency is not installed.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "the MCP server needs the optional 'mcp' dependency; "
            "install it with: pip install 'melody[mcp]'"
        ) from exc

    server = FastMCP("melody")
    server.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)(review_diff_tool)
    server.tool(
        name=TOOL_NAME_ASSUMPTIONS,
        description=TOOL_DESCRIPTION_ASSUMPTIONS,
    )(check_assumptions_tool)
    return server


def main() -> int:
    """Run the MCP server over stdio.

    Returns:
        Process exit code.
    """
    import sys

    try:
        build_server()
    except RuntimeError as exc:
        print(f"melody-mcp: {exc}", file=sys.stderr)
        return 2
    # FastMCP.run() blocks on stdio and does not return a code.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
