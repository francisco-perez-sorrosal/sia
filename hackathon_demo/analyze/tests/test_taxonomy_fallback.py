"""Offline unit tests for the R2 keyword-fallback classifier.

These tests MUST pass with no env vars and no network -- the demo is a static
replay and the classifier's deterministic path is the offline guarantee. They lock
in two things:

  1. The keyword fallback maps known improvement-plan prose to the right buckets
     (a self-repair/try-except change -> retry/robustness; a schema-injection /
     few-shot change -> prompt-restructure) and produces a non-empty `counts`.
  2. The off-by-one / missing-file contract: a generation with no improvement.md
     yields an empty taxonomy and never crashes.

The bucket strings are imported from `taxonomy` (the single source of truth) rather
than hardcoded here, so a bucket rename can't leave a stale literal in the test.
"""

from __future__ import annotations

from pathlib import Path

from hackathon_demo.analyze import analyze_improvements as ai
from hackathon_demo.analyze import taxonomy

# A realistic improvement.md fragment with two clearly distinct changes: a SQL
# self-repair loop (retry/robustness) and a schema-DDL / few-shot prompt change
# (prompt-restructure). Modeled on the real runs/run_1/gen_2 + gen_3 files.
_SAMPLE_IMPROVEMENT_MD = """\
# Gen-2 Improvement Plan

## Gen-2 Improvements

### Fix 1 -- SQL execution validation + self-repair loop
Wrapped the generated SQL in try/except and executed it read-only first; on a
sqlite3 error, re-prompt the model with the error message and retry up to 2 times.

### Fix 2 -- Inject table schema DDL and few-shot examples into the prompt
Injected the full table schema DDL into the system prompt and added few-shot
examples demonstrating the INNER JOIN and COUNT(DISTINCT) patterns.

## What Was Not Changed
- The output contract (responses.json).
"""


def test_self_repair_block_classifies_as_retry_robustness() -> None:
    result = ai.classify_by_keywords(_SAMPLE_IMPROVEMENT_MD)
    assert taxonomy.RETRY_ROBUSTNESS in result["counts"]
    assert result["counts"][taxonomy.RETRY_ROBUSTNESS] >= 1


def test_schema_injection_block_classifies_as_prompt_restructure() -> None:
    result = ai.classify_by_keywords(_SAMPLE_IMPROVEMENT_MD)
    assert taxonomy.PROMPT_RESTRUCTURE in result["counts"]


def test_counts_are_non_empty_and_primary_is_set() -> None:
    result = ai.classify_by_keywords(_SAMPLE_IMPROVEMENT_MD)
    assert result["counts"], "keyword fallback produced an empty counts map"
    assert result["primary_bucket"] in taxonomy.ALL_BUCKETS
    # Two distinct changes -> two classified entries.
    assert len(result["changes"]) >= 2


def test_what_was_not_changed_section_is_ignored_as_a_change() -> None:
    # The "What Was Not Changed" section has no change-heading, so split_changes
    # must not emit it as a discrete change block.
    summaries = [c["summary"] for c in ai.classify_by_keywords(_SAMPLE_IMPROVEMENT_MD)["changes"]]
    assert not any("not changed" in s.lower() for s in summaries)


def test_every_assigned_bucket_is_valid() -> None:
    for change in ai.classify_by_keywords(_SAMPLE_IMPROVEMENT_MD)["changes"]:
        assert change["bucket"] in taxonomy.ALL_BUCKETS


def test_missing_improvement_md_yields_empty_taxonomy(tmp_path: Path) -> None:
    # A run with a gen dir but no improvement.md (the gen_1 off-by-one) must not crash.
    (tmp_path / "gen_1").mkdir()
    result = ai.classify_run(tmp_path, use_llm=False)
    assert result == {1: ai.empty_taxonomy()}
    assert result[1]["changes"] == []
    assert result[1]["primary_bucket"] is None
    assert result[1]["counts"] == {}


def test_empty_improvement_md_is_treated_as_missing(tmp_path: Path) -> None:
    gen = tmp_path / "gen_2"
    gen.mkdir()
    (gen / "improvement.md").write_text("   \n", encoding="utf-8")
    result = ai.classify_run(tmp_path, use_llm=False)
    assert result[2] == ai.empty_taxonomy()


def test_classify_run_keys_by_numeric_gen_index(tmp_path: Path) -> None:
    for n in (1, 2, 10):
        gen = tmp_path / f"gen_{n}"
        gen.mkdir()
        if n != 1:
            (gen / "improvement.md").write_text(_SAMPLE_IMPROVEMENT_MD, encoding="utf-8")
    result = ai.classify_run(tmp_path, use_llm=False)
    assert set(result) == {1, 2, 10}
    assert result[1] == ai.empty_taxonomy()
    assert result[10]["counts"]  # gen_10 sorted/parsed numerically, not lexically
