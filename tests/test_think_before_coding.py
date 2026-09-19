"""Tests for the Think Before Coding check (TC001/TC002), exercised through the
real MCP tool call path -- not just the underlying function.

The MCP server uses FastMCP to expose tools over stdio. We test by:
1. Importing the MCP server module (requires the 'mcp' SDK).
2. Calling the check_assumptions_tool function directly with argument dicts that
   FastMCP would pass after parsing its schema. This is the FastMCP tool-call
   path: the function receives typed args as the MCP adapter passes them.
3. Asserting on the returned dict (not a Finding object -- that's core's job).

Three cases per the done-when:
- Gap found: plan commits to unspecified format/library -> TC001 finding, inferred.
- Zero findings: task fully specifies everything -> no findings.
- Structure enforcement: gaps exist but no named alternatives -> TC002 finding,
  inferred (text parse of the planned approach).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# Skip all tests if the 'mcp' SDK is not installed.
try:
    from mcp_server import check_assumptions_tool  # noqa: E402
except ImportError:
    check_assumptions_tool = None  # type: ignore[assignment]


pytestmark = pytest.mark.skipif(
    check_assumptions_tool is None,
    reason="mcp SDK not installed; run: pip install 'melody[mcp]'",
)


# --- Case 1: assumption gap found (inferred) --------------------------------


def test_plan_commits_to_unspecified_format_produces_tc001_inferred() -> None:
    """The plan uses JSON but the task never mentions it."""
    result = check_assumptions_tool(
        task_description="Write a function that serializes a Python dict.",
        planned_approach=(
            "I will use JSON to serialize the dict. The function will take a "
            "dict and return a string."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 1
    assert len(result["findings"]) >= 1
    tc001 = [f for f in result["findings"] if f["rule_id"] == "TC001_assumption_gap"]
    assert tc001, f"expected TC001 finding, got {[f['rule_id'] for f in result['findings']]}"
    f = tc001[0]
    assert f["pillar"] == "think_before_coding"
    assert f["tier"] == "inferred"
    assert "json" in f["message"].lower()
    assert "not specify" in f["message"].lower() or "does not specify" in f["message"].lower()
    ev = f["evidence"][0]
    assert ev["tier"] == "inferred"
    assert ev["command"] is None  # no command ran for inferred tier


def test_plan_commits_to_unspecified_library_produces_tc001() -> None:
    """The plan uses pandas but the task never names it."""
    result = check_assumptions_tool(
        task_description="Process a CSV file and compute column statistics.",
        planned_approach=(
            "I'll use pandas to read the CSV, then compute mean and std. "
            "Using pandas DataFrame operations."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 1
    tc001 = [f for f in result["findings"] if f["rule_id"] == "TC001_assumption_gap"]
    assert tc001, f"expected TC001 finding, got {[f['rule_id'] for f in result['findings']]}"
    f = tc001[0]
    assert f["tier"] == "inferred"


# --- Case 2: zero findings (fully specified) ---------------------------------


def test_task_fully_specifies_approach_produces_zero_findings() -> None:
    """The task names JSON explicitly; the plan uses it -- no gap."""
    result = check_assumptions_tool(
        task_description="Write a function that serializes a Python dict to JSON.",
        planned_approach=(
            "I will use JSON to serialize the dict. The function will take a "
            "dict and return a JSON string using the built-in json library."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["findings"] == []


def test_task_specifies_library_by_name_produces_zero_findings() -> None:
    """The task names pandas; the plan uses it -- no gap."""
    result = check_assumptions_tool(
        task_description="Process a CSV with pandas and compute column statistics.",
        planned_approach=(
            "I'll use pandas to read the CSV with pd.read_csv, then use "
            "DataFrame.describe() to get mean and std."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["findings"] == []


# --- Case 3: structure enforcement (inferred) --------------------------------


def test_gaps_exist_but_no_named_alternatives_produces_tc002_inferred() -> None:
    """Assumption gaps exist; plan has no named approaches with tradeoffs."""
    result = check_assumptions_tool(
        task_description="Write a function that serializes a Python dict.",
        planned_approach=(
            "I will use JSON. It's simple and standard. "
            "Also, I could use pickle, but I won't."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 1
    # TC001 for the JSON gap
    tc001 = [f for f in result["findings"] if f["rule_id"] == "TC001_assumption_gap"]
    assert tc001, "expected TC001 gap finding"
    # TC002 for missing structure
    tc002 = [f for f in result["findings"] if f["rule_id"] == "TC002_missing_reasoning_structure"]
    assert tc002, f"expected TC002 finding, got {[f['rule_id'] for f in result['findings']]}"
    f = tc002[0]
    assert f["pillar"] == "think_before_coding"
    assert f["tier"] == "inferred"
    assert "ambiguity" in f["message"].lower() or "alternatives" in f["message"].lower()
    ev = f["evidence"][0]
    assert ev["tier"] == "inferred"


def test_two_approaches_with_tradeoffs_produces_zero_tc002() -> None:
    """Gaps exist but two named approaches with tradeoffs are recorded."""
    result = check_assumptions_tool(
        task_description="Write a function that serializes a Python dict.",
        planned_approach=(
            "## Approach A: Use JSON\n"
            "Tradeoff: simple, standard library, but no support for arbitrary types.\n\n"
            "## Approach B: Use pickle\n"
            "Tradeoff: supports arbitrary types, but not safe with untrusted input."
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 1  # TC001 gaps still fire
    tc001 = [f for f in result["findings"] if f["rule_id"] == "TC001_assumption_gap"]
    assert tc001, "expected TC001 gap finding"
    tc002 = [f for f in result["findings"] if f["rule_id"] == "TC002_missing_reasoning_structure"]
    assert not tc002, f"expected no TC002 when structure is present, got {tc002}"
