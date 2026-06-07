"""Offline contract tests for `flatten.py` (the keystone demo_data.json builder).

These MUST pass with no env vars and no network -- flatten uses the deterministic
keyword taxonomy path by default. They lock in the contract the web app depends on:

  1. Every top-level key is present (run_id, task, generations, headline, tour_steps).
  2. headline.self_repair_gen is present AND correctly identifies the generation that
     both adds SQL self-repair and steps the accuracy curve up the most.
  3. Generations are numerically sorted (gen_10 follows gen_9, not lexicographically).
  4. Each generation carries a taxonomy.counts object.
  5. A generation with a missing results.json (the gen_1 off-by-one) is tolerated --
     it produces a valid entry with null metrics, never a crash.

A tiny synthetic run is built in tmp_path so the tests own their fixture and leave
no trace; bucket strings come from `taxonomy` (the single source of truth).
"""

from __future__ import annotations

import json
from pathlib import Path

from hackathon_demo.export import flatten

# A weak baseline target. Gen-2 adds a schema-injection prompt change (no self-repair);
# gen-3 adds the try/except sqlite3 self-repair loop -- the "wow" frame.
_TARGET_GEN1 = "def run():\n    sql = model(question)\n    execute(sql)\n"
_TARGET_GEN2 = "def run():\n    prompt = schema_ddl + question\n    sql = model(prompt)\n    execute(sql)\n"
_TARGET_GEN3 = (
    "def run():\n"
    "    prompt = schema_ddl + question\n"
    "    sql = model(prompt)\n"
    "    try:\n"
    "        execute(sql)\n"
    "    except sqlite3.Error as e:\n"
    "        sql = model(prompt + str(e))  # retry on sqlite error\n"
    "        execute(sql)\n"
)

_IMPROVEMENT_GEN2 = (
    "# Gen-2 Improvement Plan\n\n"
    "### Fix 1 -- Inject table schema DDL into the prompt\n"
    "Injected the full schema DDL and few-shot examples into the system prompt.\n"
)
_IMPROVEMENT_GEN3 = (
    "# Gen-3 Improvement Plan\n\n"
    "### Fix 1 -- SQL self-repair loop\n"
    "Wrapped execution in try/except and re-prompt the model on a sqlite3 error, retry up to 2 times.\n"
)


def _write_gen(run_dir: Path, idx: int, *, source: str, accuracy_pct: float | None, improvement: str | None) -> None:
    """Create a gen_<idx> dir with target_agent.py and optional results/improvement."""
    gen_dir = run_dir / f"gen_{idx}"
    gen_dir.mkdir(parents=True)
    (gen_dir / "target_agent.py").write_text(source, encoding="utf-8")
    if accuracy_pct is not None:
        results = {
            "total_questions": 10,
            "correct": round(accuracy_pct / 10),
            "exec_error": 0,
            "accuracy": accuracy_pct / 100,
            "accuracy_percent": accuracy_pct,
            "timestamp": "2026-06-06T00:00:00",
            "results": [],
        }
        (gen_dir / "results.json").write_text(json.dumps(results), encoding="utf-8")
    if improvement is not None:
        (gen_dir / "improvement.md").write_text(improvement, encoding="utf-8")


def _build_climbing_run(tmp_path: Path) -> Path:
    """A 3-gen run: gen_1 has NO results.json (off-by-one); gen_3 self-repairs and climbs."""
    run_dir = tmp_path / "run_x"
    run_dir.mkdir()
    _write_gen(run_dir, 1, source=_TARGET_GEN1, accuracy_pct=None, improvement=None)
    _write_gen(run_dir, 2, source=_TARGET_GEN2, accuracy_pct=40.0, improvement=_IMPROVEMENT_GEN2)
    _write_gen(run_dir, 3, source=_TARGET_GEN3, accuracy_pct=80.0, improvement=_IMPROVEMENT_GEN3)
    return run_dir


def test_all_top_level_keys_present(tmp_path: Path) -> None:
    data = flatten.flatten_run(_build_climbing_run(tmp_path))
    for key in ("run_id", "task", "generations", "headline", "tour_steps"):
        assert key in data, f"missing top-level key: {key}"
    assert data["run_id"] == "run_x"


def test_self_repair_gen_detected_at_the_climbing_generation(tmp_path: Path) -> None:
    data = flatten.flatten_run(_build_climbing_run(tmp_path))
    assert "self_repair_gen" in data["headline"]
    # gen_3 adds the try/except sqlite retry AND steps 40 -> 80, the largest step-up.
    assert data["headline"]["self_repair_gen"] == 3


def test_generations_numerically_sorted(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_sort"
    run_dir.mkdir()
    # Create gen_2, gen_10, gen_1 out of order; expect numeric (1, 2, 10) not lexical.
    for idx in (2, 10, 1):
        _write_gen(run_dir, idx, source=_TARGET_GEN1, accuracy_pct=50.0, improvement=None)
    data = flatten.flatten_run(run_dir)
    gens = [g["gen"] for g in data["generations"]]
    assert gens == [1, 2, 10]


def test_each_generation_has_taxonomy_counts(tmp_path: Path) -> None:
    data = flatten.flatten_run(_build_climbing_run(tmp_path))
    for gen in data["generations"]:
        assert "taxonomy" in gen
        assert "counts" in gen["taxonomy"]
        assert isinstance(gen["taxonomy"]["counts"], dict)


def test_missing_results_json_yields_null_metrics_without_crashing(tmp_path: Path) -> None:
    data = flatten.flatten_run(_build_climbing_run(tmp_path))
    gen1 = next(g for g in data["generations"] if g["gen"] == 1)
    assert gen1["metrics"] is None
    assert gen1["diff_from_prev"] is None  # first gen has no predecessor
    assert gen1["improvement_md"] is None
