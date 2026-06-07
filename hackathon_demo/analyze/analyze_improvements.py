#!/usr/bin/env python3
"""R2 improvement classifier: per-CHANGE taxonomy over a SIA run's `improvement.md` files.

This is a **read-only** consumer of `runs/<run>/gen_*/improvement.md`. It never writes
into `runs/`. For each generation's `improvement.md` it produces ONE taxonomy object:

    {
      "changes": [ {"summary": "...", "bucket": "retry/robustness"}, ... ],
      "primary_bucket": "retry/robustness" | null,
      "counts": { "retry/robustness": 2, "prompt-restructure": 1 }
    }

This matches the per-gen `taxonomy` shape in the `demo_data.json` contract that
`flatten.py` (a later step) consumes -- per-CHANGE richness, NOT one label per
generation. One `improvement.md` typically yields ~3-8 classified changes.

Two classification paths share the bucket set from `taxonomy.py`:

  - LLM-primary (Nebius structured output): one structured-output call per
    `improvement.md` returns the full list of changes. Requires NEBIUS_API_KEY +
    NEBIUS_API_BASE; the model id defaults to the cheap catalog-resolved id from
    SIA_TASK_MODEL, overridable via R2_CLASSIFIER_MODEL.

  - Keyword fallback (deterministic, offline): splits the markdown into change
    sections and classifies each by the keyword sets in `taxonomy.py`. Requires no
    network and no key -- the demo is a static replay, so the tool must work fully
    offline via this path.

Off-by-one: gen_1 has NO improvement.md (the first target agent is generated, not
improved). `classify_run` emits an empty taxonomy for any generation whose
improvement.md is missing or empty, and never crashes.

Usage:
    python analyze_improvements.py --run-dir runs/run_1                # keyword fallback
    python analyze_improvements.py --run-dir runs/run_1 --use-llm      # LLM-primary
    python analyze_improvements.py --run-dir runs/run_1 --use-llm --compare
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from hackathon_demo.analyze import taxonomy
except ImportError:  # pragma: no cover - allows `python analyze_improvements.py` from the dir
    import taxonomy  # type: ignore[no-redef]

# A generation contributes a change list only if its improvement.md exists and is
# non-trivial; below this many characters we treat it as empty (the off-by-one gen_1
# case, or a stub file).
_MIN_IMPROVEMENT_CHARS = 20

# Markdown headings that delimit a single discrete change inside an improvement.md.
# These files are authored as "### Fix N -- ...", "### Improvement N -- ...", or
# "### Issue N -- ..." sections; we split on those to get per-change granularity.
_CHANGE_HEADING = re.compile(
    r"^#{2,4}\s+(?:fix|improvement|issue|change|fix\s*\d+|improvement\s*\d+)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

# The classifier model id: the cheap Nebius catalog id, overridable for R2 specifically.
_DEFAULT_MODEL_ENV = "SIA_TASK_MODEL"
_OVERRIDE_MODEL_ENV = "R2_CLASSIFIER_MODEL"


# ── Empty-taxonomy helper (the off-by-one / missing-file contract) ──────────────


def empty_taxonomy() -> dict[str, Any]:
    """The taxonomy object for a generation with no (or empty) improvement.md."""
    return {"changes": [], "primary_bucket": None, "counts": {}}


def _aggregate(changes: list[dict[str, str]]) -> dict[str, Any]:
    """Build the {changes, primary_bucket, counts} object from a list of changes."""
    counts: dict[str, int] = {}
    for change in changes:
        bucket = change["bucket"]
        counts[bucket] = counts.get(bucket, 0) + 1
    primary_bucket = max(counts, key=lambda b: counts[b]) if counts else None
    return {"changes": changes, "primary_bucket": primary_bucket, "counts": counts}


# ── Keyword fallback (deterministic, offline) ──────────────────────────────────


def split_changes(improvement_md: str) -> list[str]:
    """Split an improvement.md into discrete change blocks by their section headings.

    Returns the text of each "### Fix/Improvement/Issue ..." section (heading +
    body up to the next such heading). If no change headings are present, returns
    the whole document as a single block so a flat improvement.md still classifies.
    """
    matches = list(_CHANGE_HEADING.finditer(improvement_md))
    if not matches:
        stripped = improvement_md.strip()
        return [stripped] if stripped else []
    blocks: list[str] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(improvement_md)
        block = improvement_md[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _summarize_block(block: str) -> str:
    """Derive a short summary for a change block: its heading line, cleaned up."""
    first_line = block.lstrip("#").strip().splitlines()[0]
    return first_line.strip()


def classify_by_keywords(improvement_md: str) -> dict[str, Any]:
    """Classify every discrete change in an improvement.md via the keyword fallback.

    Unclassifiable blocks (no keyword hit) are dropped from the change list rather
    than forced into a bucket -- a wrong bucket pollutes the headline more than a
    missing one. Returns the {changes, primary_bucket, counts} contract object.
    """
    changes: list[dict[str, str]] = []
    for block in split_changes(improvement_md):
        bucket = taxonomy.classify_change_by_keywords(block)
        if bucket is None:
            continue
        changes.append({"summary": _summarize_block(block), "bucket": bucket})
    return _aggregate(changes)


# ── LLM-primary path (Nebius structured output) ────────────────────────────────


def resolve_classifier_model() -> str:
    """Resolve the R2 classifier model id (R2_CLASSIFIER_MODEL overrides SIA_TASK_MODEL)."""
    override = os.environ.get(_OVERRIDE_MODEL_ENV)
    if override:
        return override
    model = os.environ.get(_DEFAULT_MODEL_ENV)
    if not model:
        raise RuntimeError(f"No classifier model id set: provide {_OVERRIDE_MODEL_ENV} or {_DEFAULT_MODEL_ENV}.")
    return model


def _classification_json_schema() -> dict[str, Any]:
    """The json_schema for the structured-output call: a list of {summary, bucket, code}.

    `code` is the literal implementing diff lines for the change (with their +/-
    markers preserved), one per array entry. It may be empty when a change has no
    clear code line (a pure prose/rule change).
    """
    return {
        "name": "improvement_taxonomy",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "summary": {"type": "string"},
                            "bucket": {"type": "string", "enum": list(taxonomy.ALL_BUCKETS)},
                            "code": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The literal implementing diff lines for this change "
                                "(with +/- markers), or empty if the change has no clear code line.",
                            },
                        },
                        "required": ["summary", "bucket", "code"],
                    },
                }
            },
            "required": ["changes"],
        },
    }


def _classifier_system_prompt() -> str:
    """The system prompt: defines the six buckets so the model classifies consistently."""
    return (
        "You classify the discrete harness changes a self-improving SQL agent made in "
        "one generation. You are given the generation's improvement plan AND the unified "
        "diff of the actual code change. Return EVERY distinct change as "
        "{summary, bucket, code}. Use ONLY these buckets:\n"
        f"- {taxonomy.PARSER_HARDENING}: output parsing, fence stripping, tolerant key lookup.\n"
        f"- {taxonomy.RETRY_ROBUSTNESS}: SQL self-repair loops, API retry/backoff, timeouts.\n"
        f"- {taxonomy.VALIDATION}: pre-execution checks, semantic self-checks, guards, token caps.\n"
        f"- {taxonomy.NEW_TOOL}: a new capability (schema DDL extraction, a second model call, helpers).\n"
        f"- {taxonomy.PROMPT_RESTRUCTURE}: system-prompt rules, schema injection, few-shot, thinking mode.\n"
        f"- {taxonomy.TASK_SPECIFIC_HACK}: overfitting/benchmark-gaming (hardcoding gold, gaming the grader).\n"
        "summary is one concise clause. Return the changes in document order. "
        "Ignore 'What was NOT changed' sections -- those are non-changes.\n\n"
        "For `code`: quote the actual statements from the DIFF that IMPLEMENT this change "
        "-- the real lines (e.g. `MAX_TOKENS = 2048`, `temperature=0,`, the `try`/`except` "
        "block, a new prompt-rule string), with their +/- diff markers preserved, 3-12 "
        "lines max. CRITICAL: quote the implementing code, NOT the module docstring and NOT "
        "the numbered change-summary header comments at the top of the file that merely "
        "DESCRIBE the changes. If a change has no clear code line (a pure prose/rule change), "
        "return an empty `code` array."
    )


def _user_message(improvement_md: str, diff_text: str | None) -> str:
    """Build the user message: the improvement plan plus the gen's code diff (if any)."""
    if not diff_text:
        return improvement_md
    return f"IMPROVEMENT PLAN:\n{improvement_md}\n\nCODE DIFF (this generation):\n{diff_text}"


def _code_lines(raw: Any) -> list[str]:
    """Normalize the model's `code` field to a clean list of diff-line strings (capped)."""
    if not isinstance(raw, list):
        return []
    lines = [str(line) for line in raw if str(line).strip()]
    return lines[:12]


def classify_by_llm(improvement_md: str, *, diff_text: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Classify an improvement.md via one Nebius structured-output call.

    Constructs a raw openai client against NEBIUS_API_BASE/NEBIUS_API_KEY (same wire
    as the target scaffold) and makes one json_schema-constrained call. When `diff_text`
    is given, the model attributes each change to its real implementing code lines (the
    per-change `code` field). Raises on auth/network failure -- the caller
    (`classify_run`) decides whether to fall back.
    """
    from openai import OpenAI  # local import: keep the offline fallback import-free

    base_url = os.environ.get("NEBIUS_API_BASE")
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError("NEBIUS_API_BASE / NEBIUS_API_KEY required for the LLM path.")
    client = OpenAI(base_url=base_url, api_key=api_key)
    response = client.chat.completions.create(
        model=model or resolve_classifier_model(),
        messages=[
            {"role": "system", "content": _classifier_system_prompt()},
            {"role": "user", "content": _user_message(improvement_md, diff_text)},
        ],
        response_format={"type": "json_schema", "json_schema": _classification_json_schema()},
        temperature=0.0,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    changes = [
        {"summary": str(c["summary"]), "bucket": str(c["bucket"]), "code": _code_lines(c.get("code"))}
        for c in payload.get("changes", [])
        if c.get("bucket") in taxonomy.ALL_BUCKETS
    ]
    return _aggregate(changes)


# ── Run-level driver ───────────────────────────────────────────────────────────


def _read_improvement(gen_dir: Path) -> str | None:
    """Read gen_dir/improvement.md; return None if absent or trivially empty."""
    path = gen_dir / "improvement.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text if len(text.strip()) >= _MIN_IMPROVEMENT_CHARS else None


def _gen_index(gen_dir: Path) -> int:
    """Extract the numeric generation index from a gen_<N> directory name."""
    match = re.search(r"(\d+)$", gen_dir.name)
    return int(match.group(1)) if match else 0


def classify_run(
    run_dir: Path, *, use_llm: bool = False, diffs: dict[int, str | None] | None = None
) -> dict[int, dict[str, Any]]:
    """Classify every generation in a run, keyed by numeric generation index.

    Missing/empty improvement.md (the gen_1 off-by-one, or a stub) -> empty taxonomy.
    When use_llm is True, the LLM path is attempted per gen with a per-gen keyword
    fallback if the call fails -- the run never aborts on a single failed call. When
    `diffs` maps a gen index to that gen's unified diff, the LLM path attributes each
    change to its real implementing code lines (the per-change `code` field).
    """
    gen_dirs = sorted(run_dir.glob("gen_*"), key=_gen_index)
    result: dict[int, dict[str, Any]] = {}
    for gen_dir in gen_dirs:
        if not gen_dir.is_dir():
            continue
        idx = _gen_index(gen_dir)
        improvement_md = _read_improvement(gen_dir)
        if improvement_md is None:
            result[idx] = empty_taxonomy()
            continue
        diff_text = diffs.get(idx) if diffs else None
        result[idx] = _classify_one(improvement_md, use_llm=use_llm, diff_text=diff_text)
    return result


def _classify_one(improvement_md: str, *, use_llm: bool, diff_text: str | None = None) -> dict[str, Any]:
    """Classify one improvement.md, LLM-first with a keyword fallback on failure."""
    if not use_llm:
        return classify_by_keywords(improvement_md)
    try:
        return classify_by_llm(improvement_md, diff_text=diff_text)
    except Exception as exc:
        # Any LLM/auth/network failure falls back to the deterministic keyword path
        # so a single bad call never aborts the run (the demo must work offline).
        print(f"  [LLM path failed: {exc}; using keyword fallback]")
        return classify_by_keywords(improvement_md)


def _print_run(run_dir: Path, *, use_llm: bool, compare: bool) -> None:
    """CLI helper: classify a run and print per-gen results (+ LLM-vs-keyword compare)."""
    llm_result = classify_run(run_dir, use_llm=use_llm) if use_llm else {}
    kw_result = classify_run(run_dir, use_llm=False)
    chosen = llm_result if use_llm else kw_result
    for gen in sorted(chosen):
        tax = chosen[gen]
        print(f"gen_{gen}: primary={tax['primary_bucket']} counts={tax['counts']}")
        for change in tax["changes"]:
            print(f"    [{change['bucket']}] {change['summary']}")
        if compare and use_llm:
            kw_counts = kw_result.get(gen, empty_taxonomy())["counts"]
            agree = "AGREE" if kw_counts == tax["counts"] else "DIFFER"
            print(f"    keyword counts={kw_counts}  -> {agree}")


def main() -> None:
    parser = argparse.ArgumentParser(description="R2 per-change improvement taxonomy over a SIA run.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Path to runs/<run> (read-only).")
    parser.add_argument("--use-llm", action="store_true", help="Use the Nebius LLM path (else keyword).")
    parser.add_argument("--compare", action="store_true", help="Print keyword vs LLM agreement per gen.")
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        raise SystemExit(f"Run dir not found: {args.run_dir}")
    _print_run(args.run_dir, use_llm=args.use_llm, compare=args.compare)


if __name__ == "__main__":
    main()
