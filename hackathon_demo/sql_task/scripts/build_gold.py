#!/usr/bin/env python3
"""Step A2 build: expand gold.json and regenerate sample.json["held_out"] in lockstep.

One-shot offline build script for the SIA SQL task. It rebuilds the held-out
ground truth from two IMMUTABLE sources so the grader gold and the agent-facing
question set cannot drift -- and so re-running is idempotent:

1. The pristine original 48 rows from ``data/private/gold_core48.json`` are kept
   verbatim and tagged ``"core": true`` (the comparability anchor for the
   historical 48-item ladders).
2. The validated new rows from ``tmp/spider_candidates.json`` (produced by the
   Step A1 spike) are appended with NO ``"core"`` key.

``gold.json`` is pure derived OUTPUT -- the script never reads it as an input, so
running this twice yields byte-identical output (the idempotency invariant the
MANIFEST sha256 depends on).

The merged list is written to ``data/private/gold.json``. Then
``data/public/sample.json["held_out"]`` is regenerated from that same merged
list as inputs only (``question_id``/``db_id``/``question``) -- ``gold_sql``,
``hardness``, and ``core`` are stripped so no gold leaks into the public split.
``few_shot`` and ``description`` are left untouched.

Run:  python hackathon_demo/sql_task/scripts/build_gold.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file; no CWD assumptions).
# scripts/ -> sql_task/ is the task root.
# ---------------------------------------------------------------------------
TASK_ROOT = Path(__file__).resolve().parent.parent
# Immutable input: the pristine original 48 (no "core" key). Read-only here.
CORE_PATH = TASK_ROOT / "data" / "private" / "gold_core48.json"
# Derived output: the merged 220-row gold (never read as input -> idempotent).
GOLD_PATH = TASK_ROOT / "data" / "private" / "gold.json"
SAMPLE_PATH = TASK_ROOT / "data" / "public" / "sample.json"

# Input: worktree-root tmp/ candidate file (produced by the Step A1 spike).
REPO_ROOT = TASK_ROOT.parent.parent
CANDIDATES_PATH = REPO_ROOT / "tmp" / "spider_candidates.json"

# Inputs-only keys carried into the public held_out split (no gold leaked).
HELD_OUT_KEYS = ("question_id", "db_id", "question")


def load_json(path: Path) -> Any:
    """Load a JSON file, failing fast with a clear message if it is missing."""
    if not path.is_file():
        raise FileNotFoundError(f"Required input not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_gold(core48: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge the verbatim core 48 (tagged ``core``) with the validated new rows."""
    core = [{**row, "core": True} for row in core48]
    return core + list(candidates)


def build_held_out(gold: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the gold list to the public inputs-only held_out set."""
    return [{key: row[key] for key in HELD_OUT_KEYS} for row in gold]


def write_json(path: Path, data: Any) -> None:
    """Write JSON with stable 2-space indentation and a trailing newline."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main() -> None:
    core48 = load_json(CORE_PATH)
    candidates = load_json(CANDIDATES_PATH)

    gold = build_gold(core48, candidates)
    write_json(GOLD_PATH, gold)

    sample = load_json(SAMPLE_PATH)
    sample["held_out"] = build_held_out(gold)
    write_json(SAMPLE_PATH, sample)

    core_count = sum(1 for row in gold if row.get("core"))
    print(f"Wrote {len(gold)} gold rows ({core_count} core) to {GOLD_PATH}")
    print(f"Wrote {len(sample['held_out'])} held_out rows to {SAMPLE_PATH}")


if __name__ == "__main__":
    main()
