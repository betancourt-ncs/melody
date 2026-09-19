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
