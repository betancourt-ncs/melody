"""Think Before Coding — assumption-gap detection and structured-reasoning enforcement.

Runs before code exists, mid-task. Accepts a task description and a planned
approach, detects assumption gaps (choices in the plan not grounded in the
task), and enforces that when gaps exist, the caller has recorded at least
two named approaches with stated tradeoffs.

Evidence tiers:
- **inferred**: assumption-gap findings (text/keyword analysis, no command ran).
- **inferred**: structured-reasoning findings (text parse of the planned approach,
  no command ran).

Layer: core. This module knows nothing about how melody is invoked.

Out of scope (not built here):
- Judging whether one approach is simpler than another.
- Generating alternatives itself.
- Cross-referencing with the Simplicity First complexity score.
  These are planned for a future pass; see evidence/runlog.md.
"""
from __future__ import annotations

import re
from typing import Sequence

from core.models import Evidence, EvidenceTier, Finding, Location, Pillar

RULE_ID_ASSUMPTION_GAP = "TC001_assumption_gap"
RULE_ID_MISSING_STRUCTURE = "TC002_missing_reasoning_structure"


# --- concrete-commitment patterns -----------------------------------------

# A "concrete commitment" is something the plan commits to that isn't forced
# by the task description. These patterns find them in the approach text.
#
# Each entry: (name, compiled_regex, required_context_words).
# required_context_words: if non-empty, the match only counts when at least
# one of these words appears within 5 words before or after the match.
# This cuts false positives from casual mentions ("we might use JSON") vs.
# actual commitments ("I will use JSON for serialization").
_COMMITMENT_PATTERNS: list[tuple[str, re.Pattern[str], tuple[str, ...]]] = [
    # Format / serialization / encoding
    (
        "format",
        re.compile(r"\b(json|yaml|toml|xml|csv|protobuf|msgpack|parquet)\b", re.I),
        ("use", "use ", "using", "format", "encode", "store", "output"),
    ),
    # Library / framework / tool named explicitly
    (
        "library",
        re.compile(
            r"\b(pandas|numpy|requests|aiohttp|fastapi|flask|django|sqlalchemy|"
            r"click|typer|rich|pydantic|dataclasses|attrs|pytest|unittest|"
            r"redis|mongodb|postgres|mysql|sqlite|clickhouse|duckdb|polars|"
            r"loguru|structlog|python-dotenv|tyr呼声|httpx)\b",
            re.I,
        ),
        (),
    ),
    # Data structure / abstraction
    (
        "data_structure",
        re.compile(
            r"\b(dict|list|tuple|set|frozenset|queue|stack|deque|heap|linked list|"
            r"tree|graph|hashmap|hashtable|array|bytearray|memoryview|dataclass|"
            r"namedtuple|BaseModel|pydantic model|named tuple)\b",
            re.I,
        ),
        ("use", "use ", "using", "implement", "store", "model", "structure", "as a"),
    ),
    # Error / failure behavior
    (
        "error_behavior",
        re.compile(
            r"\b(raise|throw|catch|except|finally|error|exception|fail|"
            r"retry|backoff|circuit breaker|fallback|default to|panic)\b",
            re.I,
        ),
        ("will", "shall", "must", "should", "on failure", "on error", "when"),
    ),
    # Concurrency / async patterns
    (
        "concurrency",
        re.compile(
            r"\b(async|await|thread|threading|multiprocessing|concurrent\w*|"
            r"parallel|goroutine|coroutine|event loop|futures?|asyncio)\b",
            re.I,
        ),
        ("use", "use ", "using", "implement", "with", "via", "through"),
    ),
    # Storage / persistence
    (
        "storage",
        re.compile(
            r"\b(database|db|file|filesystem|disk|s3|blob|redis|cache|memory|"
            r"persist|save|load|checkpoint|snapshot|dump)\b",
            re.I,
        ),
        ("store", "persist", "save to", "write to", "back up"),
    ),
    # Authentication / identity
    (
        "auth",
        re.compile(
            r"\b(auth|jwt|token|oauth|api.key|bearer|basic auth|permission|"
            r"role|acl|identity|principal)\b",
            re.I,
        ),
        ("use", "use ", "with", "via", "through", "check", "verify"),
    ),
]


def _find_commitments(text: str) -> list[tuple[str, str]]:
    """Return (category, matched_text) for every concrete commitment in *text*."""
    commitments: list[tuple[str, str]] = []
    words = re.split(r"\W+", text.lower())
    for i, word in enumerate(words):
        span_start = text.lower().find(word, max(0, sum(len(w) + 1 for w in words[:i])))
        span_end = span_start + len(word)

    for category, pattern, context_words in _COMMITMENT_PATTERNS:
        for m in pattern.finditer(text):
            matched = m.group()
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            surrounding = text[start:end].lower()
            if context_words:
                if not any(cw in surrounding for cw in context_words):
                    continue
            commitments.append((category, matched))
    return commitments


def _commitment_grounded(
    commitment_category: str, commitment_text: str, task_description: str
) -> bool:
    """True when the commitment is grounded in the task description.

    A commitment is grounded if its category, its literal text, or a close
    variant appears in the task description.
    """
    task_lower = task_description.lower()
    # Direct mention of the commitment text
    if commitment_text.lower() in task_lower:
        return True
    # Category-level: does the category appear in the task as a requirement?
    category_indicators = {
        "format": ("json", "yaml", "toml", "xml", "csv", "format"),
        "library": (),  # library names checked individually below
        "data_structure": ("data structure", "model", "dataclass", "structure"),
        "error_behavior": ("error", "exception", "fail", "retry", "handling"),
        "concurrency": ("async", "thread", "concurrent", "parallel"),
        "storage": ("store", "persist", "database", "file", "save", "load"),
        "auth": ("auth", "jwt", "token", "permission", "role"),
    }
    if commitment_category in category_indicators:
        if any(ind in task_lower for ind in category_indicators[commitment_category]):
            return True
    # Library: check by name
    if commitment_category == "library":
        lib_lower = commitment_text.lower()
        if lib_lower in task_lower:
            return True
        # also check if the library's full name appears (e.g. "pandas" -> "the pandas library")
        abbrevs = {"pandas": "pandas", "numpy": "numpy", "requests": "requests",
                   "pydantic": "pydantic", "dataclasses": "dataclass",
                   "pytest": "pytest", "fastapi": "fastapi", "flask": "flask"}
        if commitment_text.lower() in abbrevs:
            key = commitment_text.lower()
            if abbrevs[key] in task_lower:
                return True
    return False


def _parse_approaches(planned_approach: str) -> list[dict[str, str]]:
    """Extract named approaches and their tradeoffs from planned_approach.

    Looks for:
      1. Named approach headers: "Approach A:", "Option 1:", "[name]:", "## Name".
      2. A tradeoff section: "tradeoff", "tradeoffs", "pros/cons", "pros and cons",
         "advantages", "disadvantages", "why".

    Returns a list of {"name": "...", "tradeoff": "..."} dicts.
    An approach with no detected tradeoff section has tradeoff = "".
    """
    approaches: list[dict[str, str]] = []

    # Find named approach headers
    header_pattern = re.compile(
        r"(?:^|\n)(?:"          # start of string or newline
        r"#+\s+(.+?)\s*$|"      # markdown header: ## Name
        r"(?:approach|option|choice|alternative)?\s*"  # optional prefix
        r"(?:\*\*)?([A-Z][\w\s/-]+)(?:\*\*)?\s*"  # Bold/name: Approach A
        r"(?::|--|\.)|"         # followed by : -- .
        r"(?:^|\n)(?:-|\*|\d+\.)\s+"  # bullet or numbered list item
        r"(?:\*\*)?([A-Z][\w\s/-]+)(?:\*\*)?\s*:|"  # Bold item:
        r"\[\s*(?:\*\*)?([A-Z][\w\s/-]+)(?:\*\*)?\s*\]"  # [Approach A]
        r")",
        re.M | re.I,
    )

    tradeoff_indicators = (
        "tradeoff", "tradeoffs", "pros and cons", "pros/cons",
        "pros, cons", "advantages", "disadvantages", "benefit", "cost",
        "complexity", "trade-off", "why this", "rationale",
    )

    current_name = None
    current_body = ""

    for line in planned_approach.splitlines():
        # Try to detect a header
        header_match = header_pattern.search(line)
        if header_match:
            # Determine the matched name (first non-None group)
            matched_name = next((g for g in header_match.groups() if g), None)
            # Skip lines that look like tradeoff statements
            if matched_name and matched_name.lower().startswith("tradeoff"):
                # Treat this line as part of the current approach body
                if current_name is not None:
                    current_body += line + "\n"
                continue
            # Save previous
            if current_name is not None:
                approaches.append({"name": current_name, "tradeoff": current_body.strip()})
            # Start new approach
            current_name = matched_name.strip() if matched_name else None
            current_body = ""
            continue

        if current_name is not None:
            current_body += line + "\n"

    # Flush last
    if current_name is not None:
        approaches.append({"name": current_name, "tradeoff": current_body.strip()})

    # Fallback: if no approaches found, treat the whole text as one approach
    if not approaches:
        approaches.append({"name": "single approach", "tradeoff": planned_approach.strip()})

    # Verify each approach has a tradeoff section
    for app in approaches:
        app_lower = app["tradeoff"].lower()
        if not any(ind in app_lower for ind in tradeoff_indicators):
            app["tradeoff"] = ""
        else:
            # Extract just the tradeoff portion (from first indicator to end)
            first_idx = len(app["tradeoff"])
            for ind in tradeoff_indicators:
                idx = app_lower.find(ind)
                if idx != -1 and idx < first_idx:
                    first_idx = idx
            app["tradeoff"] = app["tradeoff"][first_idx:].strip()

    return approaches


def check_assumptions(
    task_description: str, planned_approach: str
) -> Sequence[Finding]:
    """Detect assumption gaps and enforce structured reasoning in a planned approach.

    Runs before code exists, mid-task. Two independent checks:

    1. **Assumption gaps** (inferred): every concrete commitment in
       ``planned_approach`` (a named library, a format, a data structure,
       an error behavior, etc.) is checked against ``task_description``. If the
       task never specifies that category or named thing, it is an assumption
       gap: the plan committed to something the task description does not
       require.

    2. **Structured-reasoning enforcement** (inferred): this check runs only when
       assumption gaps exist. It parses ``planned_approach`` for named approaches
       (e.g. "Approach A:", "Option 1:", "## Name") and checks each for a
       stated tradeoff section. If fewer than two named approaches each have a
       non-empty tradeoff, the finding is:
       "ambiguity detected, no alternatives recorded."

       This proves the structure was filled, not that the reasoning inside it
       was good. A plan can have two approaches with tradeoffs and still make
       the wrong choice — that judgment requires the Simplicity First complexity
       score, which is a future cross-reference, not built here.

    Args:
        task_description: The task as given, e.g. an agent prompt or issue.
        planned_approach: The plan or approach the agent wrote.

    Returns:
        Findings in ``THINK_BEFORE_CODING`` pillar.
    """
    findings: list[Finding] = []

    # --- Part 1: Assumption gaps (inferred) -----------------------------
    commitments = _find_commitments(planned_approach)
    ungrounded: list[tuple[str, str]] = []
    for category, text in commitments:
        if not _commitment_grounded(category, text, task_description):
            ungrounded.append((category, text))

    for category, text in ungrounded:
        findings.append(
            Finding(
                rule_id=RULE_ID_ASSUMPTION_GAP,
                pillar=Pillar.THINK_BEFORE_CODING,
                message=(
                    f"assumed {category} '{text}' but the task description "
                    "does not specify this; the choice was made without "
                    "surfacing it"
                ),
                location=Location(),
                evidence=(
                    Evidence(
                        tier=EvidenceTier.INFERRED,
                        summary=(
                            f"'{text}' ({category}) not grounded in task description; "
                            "detected via text/keyword analysis"
                        ),
                        detail=(
                            f"Commitment: {text!r} (category: {category})\n"
                            f"Task description: {task_description[:200]}{'…' if len(task_description) > 200 else ''}"
                        ),
                        command=None,
                    ),
                ),
            )
        )

    # --- Part 2: Structured-reasoning enforcement (inferred) ----------
    # Only runs when assumption gaps exist: if nothing was assumed without
    # grounding, the task was fully specified and there's nothing to push back on.
    if not ungrounded:
        return findings

    approaches = _parse_approaches(planned_approach)
    structured_with_tradeoffs = [
        a for a in approaches
        if a["name"] != "single approach" and a["tradeoff"]
    ]

    if len(structured_with_tradeoffs) < 2:
        finding = Finding(
            rule_id=RULE_ID_MISSING_STRUCTURE,
            pillar=Pillar.THINK_BEFORE_CODING,
            message=(
                "ambiguity detected, no alternatives recorded; "
                "when assumptions are made without task grounding, "
                "at least two named approaches with stated tradeoffs are required"
            ),
            location=Location(),
            evidence=(
                Evidence(
                    tier=EvidenceTier.INFERRED,
                    summary=(
                        f"found {len(structured_with_tradeoffs)} named approach(s) "
                        f"with tradeoffs; at least 2 required when assumption gaps exist"
                    ),
                    detail=(
                        f"Named approaches found: {[a['name'] for a in approaches]}\n"
                        f"Approaches with tradeoffs: "
                        f"{[a['name'] for a in structured_with_tradeoffs]}"
                    ),
                    command=None,
                ),
            ),
        )
        findings.append(finding)

    return findings
