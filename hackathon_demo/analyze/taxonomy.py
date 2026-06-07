"""Single source of truth for the R2 improvement taxonomy.

Every other module (the classifier, `flatten.py`, the web app via `demo_data.json`)
imports the bucket set, the SE-hygiene vs domain-reasoning mapping, and the keyword
sets from here -- there is no second definition of the buckets anywhere.

The taxonomy is the lens on the Feedback-Agent `F`: each harness change the agent
makes between generations is classified into one of six buckets. The headline split
("how much of the self-improvement is software-engineering hygiene vs genuine
domain reasoning?") is computed from per-change counts across these buckets.

Bucket families
---------------
SE-hygiene (software-engineering robustness, not SQL/domain insight):
    - parser-hardening   : fence stripping, output extraction, tolerant key lookup
    - retry/robustness   : SQL self-repair loops, API-level retry/backoff, timeouts
    - validation         : pre-execution checks, schema/result validation, guards

domain-reasoning (genuine NL->SQL / task insight):
    - new-tool           : a new capability the agent gives itself (schema DDL
                           extraction, a result inspector, a second model call)
    - prompt-restructure : schema injection, few-shot examples, JOIN/COUNT rules,
                           thinking mode -- changes to what the model is told

counted separately (reward-hacking smell test, NOT part of the SE/domain split):
    - task-specific-hack : overfitting to the benchmark (hardcoding gold answers,
                           gaming the grader, db-specific shortcuts that don't transfer)
"""

from __future__ import annotations

# ── The six buckets (the only place they are defined) ───────────────────────────

PARSER_HARDENING = "parser-hardening"
RETRY_ROBUSTNESS = "retry/robustness"
VALIDATION = "validation"
NEW_TOOL = "new-tool"
PROMPT_RESTRUCTURE = "prompt-restructure"
TASK_SPECIFIC_HACK = "task-specific-hack"

# SE-hygiene family: software robustness, not domain insight.
SE_HYGIENE_BUCKETS: tuple[str, ...] = (PARSER_HARDENING, RETRY_ROBUSTNESS, VALIDATION)

# domain-reasoning family: genuine NL->SQL / task understanding.
DOMAIN_REASONING_BUCKETS: tuple[str, ...] = (NEW_TOOL, PROMPT_RESTRUCTURE)

# Reward-hacking flag: counted on its own, never folded into the SE/domain split.
TASK_SPECIFIC_HACK_BUCKETS: tuple[str, ...] = (TASK_SPECIFIC_HACK,)

# All valid bucket labels, in a stable order (used for enum validation, JSON schema,
# and the stacked-bar segment order in the web app).
ALL_BUCKETS: tuple[str, ...] = (
    *SE_HYGIENE_BUCKETS,
    *DOMAIN_REASONING_BUCKETS,
    *TASK_SPECIFIC_HACK_BUCKETS,
)


def family_of(bucket: str) -> str:
    """Return the family name ('se-hygiene' | 'domain-reasoning' | 'task-specific-hack').

    Raises ValueError for an unknown bucket so a typo fails loudly rather than
    silently dropping out of both pct computations downstream.
    """
    if bucket in SE_HYGIENE_BUCKETS:
        return "se-hygiene"
    if bucket in DOMAIN_REASONING_BUCKETS:
        return "domain-reasoning"
    if bucket in TASK_SPECIFIC_HACK_BUCKETS:
        return "task-specific-hack"
    raise ValueError(f"Unknown taxonomy bucket: {bucket!r} (valid: {', '.join(ALL_BUCKETS)})")


# ── Keyword sets per bucket (the deterministic offline fallback) ─────────────────
#
# Ordered most-specific-signal-first within each bucket. The fallback scans the
# improvement text for these phrases; the most specific match wins (see
# classify_change_by_keywords). Phrases are matched case-insensitively as
# substrings, so keep them lowercase and discriminating -- a phrase that also
# appears in an unrelated bucket's prose causes misclassification.

KEYWORD_SETS: dict[str, tuple[str, ...]] = {
    RETRY_ROBUSTNESS: (
        "self-repair",
        "self repair",
        "repair loop",
        "repair prompt",
        "try/except",
        "try / except",
        "re-prompt",
        "reprompt",
        "retry",
        "retries",
        "backoff",
        "exponential backoff",
        "rate-limit",
        "rate limit",
        "transient",
        "timeout",
        "execution error",
        "exec error",
        "sqlite3.error",
        "on error",
    ),
    PARSER_HARDENING: (
        "fence strip",
        "fence-strip",
        "strip the fence",
        "markdown fence",
        "extract_sql",
        "extract sql",
        "parse the output",
        "parsing",
        "tolerant key",
        "flexible key",
        "key lookup",
        ".get(",
        "robust to",
        "tolerated rather than crashing",
        "unexpected",
        "malformed output",
    ),
    VALIDATION: (
        "validate",
        "validation",
        "pre-execution check",
        "semantic self-check",
        "semantic check",
        "self-check",
        "sanity check",
        "guard",
        "verify the",
        "schema validation",
        "result validation",
        "max_tokens",  # raising a cap to avoid silent truncation = a robustness guard
        "truncat",
    ),
    NEW_TOOL: (
        "new tool",
        "schema ddl extraction",
        "ddl extraction",
        "extract the schema",
        "schema extraction",
        "second model call",
        "extra model call",
        "additional call",
        "helper",
        "_call_api",
        "inspector",
        "trajectory logging",
    ),
    PROMPT_RESTRUCTURE: (
        "system prompt",
        "prompt rule",
        "prompt guidance",
        "inject",
        "injected",
        "schema injection",
        "schema into the prompt",
        "few-shot",
        "few shot",
        "canonical example",
        "hardcoded few-shot",
        "thinking mode",
        "enable_thinking",
        "join rule",
        "join type",
        "inner join",
        "count(distinct",
        "count distinct",
        "restructure the prompt",
    ),
    TASK_SPECIFIC_HACK: (
        "hardcode the answer",
        "hardcoded answer",
        "hardcoded gold",
        "memorize the test",
        "memorise the test",
        "game the grader",
        "gaming the grader",
        "overfit to the benchmark",
        "overfit to chinook",
        "test-specific",
        "answer key",
        "leak the gold",
    ),
}


def classify_change_by_keywords(text: str) -> str | None:
    """Classify one change description into a bucket by keyword match.

    Returns the best-matching bucket, or None when nothing matches (the caller
    decides how to treat an unclassifiable change). "Best" = the bucket whose
    longest matching keyword is longest overall, so a specific phrase
    ("exponential backoff") beats a generic one ("max_tokens") that happens to
    co-occur. Ties break by ALL_BUCKETS order (SE-hygiene before domain before hack).
    """
    haystack = text.lower()
    best_bucket: str | None = None
    best_len = 0
    for bucket in ALL_BUCKETS:
        for keyword in KEYWORD_SETS[bucket]:
            if keyword in haystack and len(keyword) > best_len:
                best_bucket = bucket
                best_len = len(keyword)
    return best_bucket
