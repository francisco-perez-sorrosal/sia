#!/usr/bin/env python3
"""Flatten a SIA run into the keystone `demo_data.json` the web app consumes.

This is the **decoupling seam**: the static SPA imports nothing from `sia/` and
reads only the JSON this module emits. `flatten.py` is a strictly read-only walker
over `runs/<run>/gen_*` -- it never writes into `runs/`.

For each generation it produces:
  - `gen`                  : numeric generation index (gen_10 sorts after gen_9).
  - `metrics`              : accuracy fraction + percent + correct/total, pulled
                            from `results.json` (None when the file is missing -- the
                            gen_1 off-by-one where no evaluation ran).
  - `target_agent_source`  : the full `target_agent.py` text for that generation.
  - `diff_from_prev`       : a difflib unified diff gen_{N-1} -> gen_N (None for the
                            first generation present).
  - `diff_highlights`      : new-file line numbers of ADDED lines that look like SQL
                            self-repair (try/except/retry/re-prompt/sqlite error),
                            grouped as [{kind: "self-repair", added_lines: [...]}].
  - `improvement_md`       : the full `improvement.md` text (None when missing).
  - `taxonomy`             : the R2 per-change {changes, primary_bucket, counts}
                            object (reused from `hackathon_demo.analyze`, NOT
                            reimplemented here).
  - `process_steps`        : a templated 6-step Execution->Analysis->Improvement
                            caption track (hand-authored copy is a later step).

Plus an aggregate `headline` (incl. the load-bearing `self_repair_gen`), a
templated `tour_steps[]`, and an optional `example_query`.

Robustness contract: any missing per-gen `results.json` / `improvement.md` /
`target_agent.py` is tolerated (metrics/None fields), never a crash. A generation
with no results.json (run_1's gen_1) still yields a valid generation entry.

Usage:
    python flatten.py --run-dir runs/run_1                       # keyword taxonomy
    python flatten.py --run-dir runs/run_1 --out demo_data.json  # explicit output
    python flatten.py --run-dir runs/run_2 --use-llm             # LLM taxonomy path
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any

try:
    from hackathon_demo.analyze import analyze_improvements, taxonomy
except ImportError:  # pragma: no cover - allows `python flatten.py` without an install
    import sys

    # Put the project root (parent of hackathon_demo/) on the path so the package
    # form resolves -- analyze_improvements imports `hackathon_demo.analyze.taxonomy`.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from hackathon_demo.analyze import analyze_improvements, taxonomy

# Default output: the web app's data file. Per-run names (demo_data_<run>.json)
# let a future experiment selector load multiple runs side by side.
_DEFAULT_OUT = Path("hackathon_demo/web/demo_data.json")

# Lines an agent ADDS that signal SQL self-repair -- the "wow" frame. Kept broad
# enough to catch try/except guards, retry loops, re-prompting, and sqlite errors.
_SELF_REPAIR_RE = re.compile(
    r"\btry\b|\bexcept\b|\bretry\b|\bretries\b|re-?prompt|sql error|sqlite3?\.error|sqlite3?error",
    re.IGNORECASE,
)

# The 6-step process caption track (Execution -> Analysis -> Improvement). Generic
# templates now; the hand-authored, per-gen copy is a later step. The web app
# overlays the gen's real accuracy delta onto these.
_PROCESS_STEP_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("Target writes SQL", "The target agent answers each NL question with SQL."),
    ("Verifier grades", "Each query runs read-only; results are graded against gold."),
    ("Results recorded", "Per-question correctness and accuracy are written to results.json."),
    ("Feedback analyzes", "The feedback agent reads the trajectory and finds what went wrong."),
    ("Improvement planned", "It drafts a concrete plan to harden the harness."),
    ("Harness rewritten", "The target agent's code is rewritten for the next generation."),
)


# ── Per-gen field readers (each tolerant of a missing file) ─────────────────────


def _gen_index(gen_dir: Path) -> int:
    """Numeric generation index from a gen_<N> directory name (gen_10 > gen_9)."""
    match = re.search(r"(\d+)$", gen_dir.name)
    return int(match.group(1)) if match else 0


def _read_metrics(gen_dir: Path) -> dict[str, Any] | None:
    """Pull metrics from results.json; None when absent (the gen_1 off-by-one).

    The grader writes `total_questions` (not `total`); we expose it as `total` for
    the contract while tolerating either key. Cost/token fields are emitted only
    when the grader recorded them (the SQL grader does not, so they are omitted).
    """
    path = gen_dir / "results.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    total = data.get("total") if "total" in data else data.get("total_questions")
    metrics: dict[str, Any] = {
        "accuracy": data.get("accuracy"),
        "accuracy_percent": data.get("accuracy_percent"),
        "correct": data.get("correct"),
        "total": total,
    }
    for optional in ("total_cost_usd", "total_input_tokens", "total_output_tokens"):
        if optional in data:
            metrics[optional] = data[optional]
    return metrics


def _read_target_source(gen_dir: Path) -> str | None:
    """Read the full target_agent.py text; None when absent."""
    path = gen_dir / "target_agent.py"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_improvement_md(gen_dir: Path) -> str | None:
    """Read the full improvement.md text; None when absent (gen_1 off-by-one)."""
    path = gen_dir / "improvement.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if text.strip() else None


def _read_example_query(gen_dir: Path) -> dict[str, Any] | None:
    """Pick one response from responses.json as a worked example; None if unavailable.

    Optional and must never gate: any failure returns None. responses.json is a
    list of {question_id, sql}; we surface the first with non-empty SQL and mark it
    passed=True only when the grader's per-result row says so.
    """
    path = gen_dir / "responses.json"
    if not path.is_file():
        return None
    try:
        responses = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(responses, list):
        return None
    passed_ids = _correct_question_ids(gen_dir)
    for item in responses:
        sql = (item.get("sql") or "").strip()
        if not sql:
            continue
        qid = item.get("question_id")
        return {"question": qid, "generated_sql": sql, "passed": qid in passed_ids}
    return None


def _correct_question_ids(gen_dir: Path) -> set[str]:
    """Question ids the grader marked correct, for the example_query passed flag."""
    path = gen_dir / "results.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    rows = data.get("results", [])
    return {r["question_id"] for r in rows if isinstance(r, dict) and r.get("correct") and "question_id" in r}


# ── Diff + self-repair highlighting ─────────────────────────────────────────────


def _unified_diff(prev_source: str | None, source: str | None) -> str | None:
    """Unified diff prev -> current target_agent.py; None when no prior gen exists."""
    if prev_source is None or source is None:
        return None
    diff = difflib.unified_diff(
        prev_source.splitlines(),
        source.splitlines(),
        fromfile="prev/target_agent.py",
        tofile="target_agent.py",
        lineterm="",
    )
    return "\n".join(diff)


def _self_repair_highlights(diff_text: str | None) -> list[dict[str, Any]]:
    """New-file line numbers of ADDED self-repair lines in a unified diff.

    Walks hunk headers (@@ -a,b +c,d @@) to track the new-file line number, then
    flags each added (`+`) line matching the self-repair regex. The web app keys
    its green-pulse off these line numbers. Empty list when no diff or no hits.
    """
    if not diff_text:
        return []
    added_lines: list[int] = []
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            new_lineno = _hunk_new_start(line)
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            if _SELF_REPAIR_RE.search(line[1:]):
                added_lines.append(new_lineno)
            new_lineno += 1
        elif line.startswith("-"):
            continue
        else:
            new_lineno += 1
    return [{"kind": "self-repair", "added_lines": added_lines}] if added_lines else []


def _hunk_new_start(header: str) -> int:
    """Parse the new-file start line from a unified-diff hunk header (@@ -a,b +c,d @@)."""
    match = re.search(r"\+(\d+)", header)
    return int(match.group(1)) if match else 0


def _process_steps(metrics: dict[str, Any] | None, prev_pct: float | None) -> list[dict[str, Any]]:
    """Templated 6-step caption track; carries the gen's real accuracy delta on step 3."""
    pct = metrics.get("accuracy_percent") if metrics else None
    delta = round(pct - prev_pct, 1) if pct is not None and prev_pct is not None else None
    steps: list[dict[str, Any]] = []
    for i, (label, caption) in enumerate(_PROCESS_STEP_TEMPLATES, start=1):
        step: dict[str, Any] = {"step": i, "label": label, "caption": caption}
        if i == 3:
            step["accuracy_percent"] = pct
            step["accuracy_delta"] = delta
        steps.append(step)
    return steps


# ── Generation assembly ─────────────────────────────────────────────────────────


def build_generation(
    gen_dir: Path,
    *,
    prev_source: str | None,
    prev_pct: float | None,
    tax: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one generation's contract entry (every field tolerant of missing data)."""
    metrics = _read_metrics(gen_dir)
    source = _read_target_source(gen_dir)
    diff = _unified_diff(prev_source, source)
    return {
        "gen": _gen_index(gen_dir),
        "metrics": metrics,
        "target_agent_source": source,
        "diff_from_prev": diff,
        "diff_highlights": _self_repair_highlights(diff),
        "improvement_md": _read_improvement_md(gen_dir),
        "taxonomy": tax,
        "process_steps": _process_steps(metrics, prev_pct),
    }


def build_generations(run_dir: Path, taxonomies: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the numerically-sorted generations[] array for a run."""
    gen_dirs = [d for d in sorted(run_dir.glob("gen_*"), key=_gen_index) if d.is_dir()]
    generations: list[dict[str, Any]] = []
    prev_source: str | None = None
    prev_pct: float | None = None
    for gen_dir in gen_dirs:
        idx = _gen_index(gen_dir)
        tax = taxonomies.get(idx, analyze_improvements.empty_taxonomy())
        gen = build_generation(gen_dir, prev_source=prev_source, prev_pct=prev_pct, tax=tax)
        generations.append(gen)
        prev_source = gen["target_agent_source"] or prev_source
        metrics = gen["metrics"]
        if metrics and metrics.get("accuracy_percent") is not None:
            prev_pct = metrics["accuracy_percent"]
    return generations


# ── Headline aggregation ────────────────────────────────────────────────────────


def _accuracy_series(generations: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """(gen, accuracy_percent) pairs for generations that recorded a metric."""
    series: list[tuple[int, float]] = []
    for gen in generations:
        metrics = gen["metrics"]
        if metrics and metrics.get("accuracy_percent") is not None:
            series.append((gen["gen"], float(metrics["accuracy_percent"])))
    return series


def _family_pcts(generations: list[dict[str, Any]]) -> tuple[int, int, int]:
    """SE-hygiene %, domain-reasoning %, and task-specific-hack count across all gens."""
    se = domain = hack = 0
    for gen in generations:
        for bucket, count in gen["taxonomy"]["counts"].items():
            family = taxonomy.family_of(bucket)
            if family == "se-hygiene":
                se += count
            elif family == "domain-reasoning":
                domain += count
            else:
                hack += count
    classified = se + domain
    se_pct = round(se / classified * 100) if classified else 0
    domain_pct = round(domain / classified * 100) if classified else 0
    return se_pct, domain_pct, hack


def _detect_self_repair_gen(generations: list[dict[str, Any]]) -> int | None:
    """The gen with a self-repair highlight whose accuracy steps up the most.

    Load-bearing: this is the "wow" frame the web app freezes on. We consider only
    generations whose diff flagged a self-repair change AND that have a positive
    accuracy step-up vs the previous measured gen; among those, the largest step-up
    wins. None when no generation both self-repairs and climbs.
    """
    series = dict(_accuracy_series(generations))
    best_gen: int | None = None
    best_step = 0.0
    prev_pct: float | None = None
    for gen in generations:
        idx = gen["gen"]
        pct = series.get(idx)
        has_self_repair = any(h["kind"] == "self-repair" for h in gen["diff_highlights"])
        if pct is not None and has_self_repair and prev_pct is not None:
            step = pct - prev_pct
            if step > best_step:
                best_step = step
                best_gen = idx
        if pct is not None:
            prev_pct = pct
    return best_gen


def build_headline(generations: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate headline incl. the load-bearing self_repair_gen."""
    series = _accuracy_series(generations)
    first = series[0][1] if series else None
    last = series[-1][1] if series else None
    gain = round(last - first, 1) if first is not None and last is not None else None
    se_pct, domain_pct, hack_count = _family_pcts(generations)
    return {
        "accuracy_first": first,
        "accuracy_last": last,
        "gain": gain,
        "se_hygiene_pct": se_pct,
        "domain_reasoning_pct": domain_pct,
        "task_specific_hack_count": hack_count,
        "self_repair_gen": _detect_self_repair_gen(generations),
    }


def build_tour_steps(headline: dict[str, Any]) -> list[dict[str, Any]]:
    """Templated guided-tour stub (~6 entries); hand-authored copy is a later step."""
    repair_gen = headline.get("self_repair_gen")
    gain = headline.get("gain")
    return [
        {
            "screen": 0,
            "gen": repair_gen,
            "caption": "An AI agent rewrote its own code and got better.",
            "hold_ms": 4000,
        },
        {"screen": 1, "gen": None, "caption": "The task: answer questions by writing SQL.", "hold_ms": 3500},
        {
            "screen": 2,
            "gen": None,
            "caption": "Each generation, a feedback agent rewrites the harness.",
            "hold_ms": 3500,
        },
        {"screen": 3, "gen": 1, "caption": "The first harness is weak and the agent struggles.", "hold_ms": 3500},
        {
            "screen": 4,
            "gen": repair_gen,
            "caption": "It adds SQL self-repair and the accuracy steps up.",
            "hold_ms": 4500,
        },
        {"screen": 6, "gen": None, "caption": f"Same model, better harness: +{gain} points.", "hold_ms": 4000},
    ]


# ── Top-level driver ────────────────────────────────────────────────────────────


def _gen_diffs(run_dir: Path) -> dict[int, str | None]:
    """Per-gen unified diff (gen_{N-1} -> gen_N), keyed by numeric gen index.

    Computed once and shared: the classifier uses it to attribute each change to its
    real implementing code (the per-change `code` field), and `build_generations`
    re-derives the same diff for the `diff_from_prev` / `diff_highlights` fields.
    """
    gen_dirs = [d for d in sorted(run_dir.glob("gen_*"), key=_gen_index) if d.is_dir()]
    diffs: dict[int, str | None] = {}
    prev_source: str | None = None
    for gen_dir in gen_dirs:
        source = _read_target_source(gen_dir)
        diffs[_gen_index(gen_dir)] = _unified_diff(prev_source, source)
        prev_source = source or prev_source
    return diffs


def flatten_run(run_dir: Path, *, use_llm: bool = False) -> dict[str, Any]:
    """Walk a run and build the full demo_data.json contract object."""
    diffs = _gen_diffs(run_dir) if use_llm else None
    taxonomies = analyze_improvements.classify_run(run_dir, use_llm=use_llm, diffs=diffs)
    generations = build_generations(run_dir, taxonomies)
    headline = build_headline(generations)
    data: dict[str, Any] = {
        "run_id": run_dir.name,
        "task": "sql",
        "generations": generations,
        "headline": headline,
        "tour_steps": build_tour_steps(headline),
    }
    example = _last_example_query(run_dir, generations)
    if example is not None:
        data["example_query"] = example
    return data


def _last_example_query(run_dir: Path, generations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Optional worked example from the last generation that has responses.json."""
    for gen in reversed(generations):
        example = _read_example_query(run_dir / f"gen_{gen['gen']}")
        if example is not None:
            return example
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten a SIA run into the keystone demo_data.json.")
    parser.add_argument("--run-dir", required=True, type=Path, help="Path to runs/<run> (read-only).")
    parser.add_argument("--out", type=Path, default=None, help=f"Output path (default: {_DEFAULT_OUT}).")
    parser.add_argument("--use-llm", action="store_true", help="Use the Nebius LLM taxonomy path (else keyword).")
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        raise SystemExit(f"Run dir not found: {args.run_dir}")

    data = flatten_run(args.run_dir, use_llm=args.use_llm)
    out_path = args.out or _DEFAULT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    series = [
        (g["gen"], g["metrics"]["accuracy_percent"])
        for g in data["generations"]
        if g["metrics"] and g["metrics"].get("accuracy_percent") is not None
    ]
    print(f"Wrote {out_path} ({len(data['generations'])} generations).")
    print(f"  accuracy series (gen, %): {series}")
    print(f"  self_repair_gen: {data['headline']['self_repair_gen']}")


if __name__ == "__main__":
    main()
