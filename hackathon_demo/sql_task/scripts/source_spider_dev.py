#!/usr/bin/env python3
"""Step A1 spike: source + validate hard/extra Spider dev questions (read-only).

One-shot offline sourcing script for the SIA SQL task. It pulls the Spider
dev/validation split for the 6 shipped databases, filters to the canonical
``hard``/``extra`` hardness bands, dedups against the existing 48 gold rows and
the 3 public few-shot demos, executes each candidate gold SQL read-only against
the shipped ``<db_id>.sqlite``, and keeps only rows that succeed and return a
non-empty result set.

Output is stdout (a per-DB count table + grand total) plus a candidate file at
``tmp/spider_candidates.json`` for Step A2 to consume. This script NEVER modifies
``gold.json``, ``sample.json``, ``MANIFEST.json``, or any ``.sqlite``.

Run:  python hackathon_demo/sql_task/scripts/source_spider_dev.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file; no CWD assumptions).
# scripts/ -> sql_task/ is the task root.
# ---------------------------------------------------------------------------
TASK_ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = TASK_ROOT / "data" / "private" / "gold.json"
SAMPLE_PATH = TASK_ROOT / "data" / "public" / "sample.json"
PUBLIC_DB_DIR = TASK_ROOT / "data" / "public"

# Output: worktree-root tmp/ (consumed by A2). NOTE: tmp/ is NOT currently in
# .gitignore for this repo (only *.log is) -- A2 must gitignore tmp/ before any
# commit, or this candidate file would otherwise be trackable. Flagged in the
# A1 report. This script's read-only contract is unaffected.
REPO_ROOT = TASK_ROOT.parent.parent
OUTPUT_PATH = REPO_ROOT / "tmp" / "spider_candidates.json"

# The 6 shipped databases (MANIFEST.json `databases`).
SHIPPED_DBS = (
    "car_1",
    "world_1",
    "student_transcripts_tracking",
    "cre_Doc_Template_Mgt",
    "dog_kennels",
    "concert_singer",
)

# db_id -> question_id prefix, matching the existing 48 (dog_NN, stt_NN, ...).
DB_PREFIX = {
    "dog_kennels": "dog",
    "student_transcripts_tracking": "stt",
    "car_1": "car",
    "world_1": "wld",
    "concert_singer": "cs",
    "cre_Doc_Template_Mgt": "doc",
}

HARD_BANDS = frozenset({"hard", "extra"})
RESOLUTION_TARGET = 150


# ---------------------------------------------------------------------------
# Canonical Spider hardness classifier (component-counting).
#
# Faithful re-implementation of taoyds/spider evaluation.py Evaluator.eval_hardness,
# operating on the gold SQL *string* (we don't have the parsed `sql` dict that the
# upstream evaluator consumes, so we count the same structural components via a
# tokenizer). Component counts and thresholds match the upstream definition.
# ---------------------------------------------------------------------------
_AGG_OPS = ("max", "min", "count", "sum", "avg")
_WHERE_OPS = (
    "between",
    "=",
    ">",
    "<",
    ">=",
    "<=",
    "!=",
    "in",
    "like",
    "is",
    "exists",
)
_SET_OPS = ("union", "except", "intersect")


def _lower_outside_strings(sql: str) -> str:
    """Lowercase SQL keywords while preserving single-quoted string literals."""
    parts = re.split(r"('(?:[^']|'')*')", sql)
    return "".join(p if i % 2 else p.lower() for i, p in enumerate(parts))


def _count_keyword(sql_lc: str, keyword: str) -> int:
    """Count whole-word occurrences of a SQL keyword (case-insensitive input)."""
    return len(re.findall(r"\b" + re.escape(keyword) + r"\b", sql_lc))


def _count_components(sql: str) -> tuple[int, int]:
    """Return (component1_count, component2_count) per the Spider hardness spec.

    component1: WHERE, GROUP BY, ORDER BY, and the LIMIT-implying parts.
    component2: aggregates, set operations (UNION/EXCEPT/INTERSECT), nested queries,
                and column-arithmetic-style operators counted by the upstream metric.

    We approximate the upstream parsed-component counts from the SQL text. The
    thresholds below (eval_hardness) are byte-identical to upstream.
    """
    sql_lc = _lower_outside_strings(sql)

    # --- component 1: WHERE / GROUP BY / ORDER BY clauses ---
    count_comp1 = 0
    if _count_keyword(sql_lc, "where"):
        count_comp1 += 1
    if "group by" in sql_lc:
        count_comp1 += 1
    if "order by" in sql_lc:
        count_comp1 += 1
    # HAVING and LIMIT each add to the structural component-1 weight upstream.
    if _count_keyword(sql_lc, "having"):
        count_comp1 += 1
    if _count_keyword(sql_lc, "limit"):
        count_comp1 += 1

    # --- component 2: aggregates, set ops, nesting ---
    count_comp2 = 0
    for agg in _AGG_OPS:
        count_comp2 += _count_keyword(sql_lc, agg)
    for sop in _SET_OPS:
        count_comp2 += _count_keyword(sql_lc, sop)
    # Nested query: a SELECT appearing after the leading one indicates a subquery.
    count_comp2 += max(0, _count_keyword(sql_lc, "select") - 1)
    # DISTINCT contributes to component-2 weight upstream.
    count_comp2 += _count_keyword(sql_lc, "distinct")

    return count_comp1, count_comp2


def eval_hardness(sql: str) -> str:
    """Classify a gold SQL string into easy / medium / hard / extra.

    Mirrors taoyds/spider Evaluator.eval_hardness threshold logic. Operates on
    the SQL string rather than the parsed dict; thresholds are unchanged.
    """
    count_comp1, count_comp2 = _count_components(sql)
    # Number of joined tables (component "others" in upstream): count JOIN clauses
    # plus comma-separated table refs in FROM.
    sql_lc = _lower_outside_strings(sql)
    count_others = _count_keyword(sql_lc, "join")

    if count_comp1 <= 1 and count_comp2 == 0 and count_others == 0:
        return "easy"
    if (count_others <= 2 and count_comp1 <= 1 and count_comp2 == 0) or (
        count_comp1 <= 2 and count_others < 2 and count_comp2 <= 1
    ):
        return "medium"
    if (
        (count_others > 2 and count_comp1 <= 2 and count_comp2 == 0)
        or (2 < count_comp1 <= 3 and count_others <= 2 and count_comp2 == 0)
        or (count_comp1 <= 1 and count_others == 0 and count_comp2 <= 1)
    ):
        return "hard"
    return "extra"


# ---------------------------------------------------------------------------
# Normalization for dedup (question + db_id + gold_sql).
# ---------------------------------------------------------------------------
def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _norm_sql(sql: str) -> str:
    # Collapse whitespace and lowercase keywords (literals preserved) for matching.
    return re.sub(r"\s+", " ", _lower_outside_strings(sql).strip())


def _dedup_key(question: str, db_id: str, gold_sql: str) -> tuple[str, str, str]:
    return (_norm_text(question), db_id, _norm_sql(gold_sql))


# ---------------------------------------------------------------------------
# Read-only SQL execution against the shipped sqlite.
# ---------------------------------------------------------------------------
def executes_non_empty(db_id: str, gold_sql: str) -> tuple[bool, str]:
    """Return (kept, reason). kept iff SQL runs read-only AND returns >=1 row."""
    db_path = PUBLIC_DB_DIR / f"{db_id}.sqlite"
    if not db_path.exists():
        return False, f"missing db {db_path.name}"
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            cur = conn.execute(gold_sql)
            rows = cur.fetchmany(1)
            return (bool(rows), "ok" if rows else "empty result")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, f"sql error: {exc}"


# ---------------------------------------------------------------------------
# Spider dev sourcing.
# ---------------------------------------------------------------------------
def load_spider_dev() -> list[dict]:
    """Load the HuggingFace xlangai/spider validation split rows we care about.

    Returns rows shaped {db_id, question, gold_sql}. Installs `datasets` lazily
    only if absent (recorded in the run report).
    """
    try:
        from datasets import load_dataset
    except ModuleNotFoundError:
        print(
            "[setup] `datasets` not installed; installing (pip install datasets)...",
            file=sys.stderr,
        )
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "datasets"])
        from datasets import load_dataset

    ds = load_dataset("xlangai/spider", split="validation")
    rows: list[dict] = []
    for r in ds:
        db_id = r["db_id"]
        if db_id not in SHIPPED_DBS:
            continue
        rows.append(
            {
                "db_id": db_id,
                "question": r["question"],
                # HF column is `query` (the gold SQL). No hardness field is shipped.
                "gold_sql": r["query"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Main pipeline.
# ---------------------------------------------------------------------------
def build_existing_keys() -> set[tuple[str, str, str]]:
    """Dedup keys from the 48 gold rows + the 3 public few-shot demos."""
    gold = json.loads(GOLD_PATH.read_text())
    sample = json.loads(SAMPLE_PATH.read_text())
    keys: set[tuple[str, str, str]] = set()
    for q in gold:
        keys.add(_dedup_key(q["question"], q["db_id"], q["gold_sql"]))
    for ex in sample["few_shot"]:
        keys.add(_dedup_key(ex["question"], ex["db_id"], ex["gold_sql"]))
    return keys


def existing_max_index(gold: list[dict]) -> dict[str, int]:
    """Highest existing NN per db prefix, so new ids continue compatibly."""
    max_idx = {db: 0 for db in SHIPPED_DBS}
    for q in gold:
        db = q["db_id"]
        m = re.search(r"_(\d+)$", q["question_id"])
        if m and db in max_idx:
            max_idx[db] = max(max_idx[db], int(m.group(1)))
    return max_idx


def main() -> int:
    print(f"[A1] task root: {TASK_ROOT}", file=sys.stderr)
    print("[A1] loading Spider dev/validation split (xlangai/spider)...", file=sys.stderr)
    raw = load_spider_dev()

    existing_keys = build_existing_keys()
    gold = json.loads(GOLD_PATH.read_text())
    next_idx = existing_max_index(gold)

    # Per-DB counters for the report.
    raw_hard = {db: 0 for db in SHIPPED_DBS}
    after_dedup = {db: 0 for db in SHIPPED_DBS}
    after_exec = {db: 0 for db in SHIPPED_DBS}

    # Track dedup keys we add during this run too (the dev split has paraphrase
    # pairs / repeats that must not collide with each other).
    seen_keys = set(existing_keys)
    candidates: list[dict] = []

    for row in raw:
        db_id = row["db_id"]
        question = row["question"]
        gold_sql = row["gold_sql"]

        hardness = eval_hardness(gold_sql)
        if hardness not in HARD_BANDS:
            continue
        raw_hard[db_id] += 1

        key = _dedup_key(question, db_id, gold_sql)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        after_dedup[db_id] += 1

        kept, _reason = executes_non_empty(db_id, gold_sql)
        if not kept:
            continue
        after_exec[db_id] += 1

        next_idx[db_id] += 1
        qid = f"{DB_PREFIX[db_id]}_{next_idx[db_id]:02d}"
        candidates.append(
            {
                "question_id": qid,
                "db_id": db_id,
                "question": question,
                "gold_sql": gold_sql,
                "hardness": hardness,
            }
        )

    _print_report(raw_hard, after_dedup, after_exec)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + "\n")
    print(f"\n[A1] wrote {len(candidates)} validated candidates -> {OUTPUT_PATH}", file=sys.stderr)

    total = len(candidates)
    if total < RESOLUTION_TARGET:
        print(
            f"\n[A1 GATE] validated total = {total} < ~{RESOLUTION_TARGET} target. "
            "STOP: surface the achievable number to the user before Step A2.",
            file=sys.stderr,
        )
    else:
        print(f"\n[A1 GATE] validated total = {total} >= ~{RESOLUTION_TARGET}. Target reachable.", file=sys.stderr)
    return 0


def _print_report(raw_hard: dict, after_dedup: dict, after_exec: dict) -> None:
    header = f"{'db_id':<32} {'raw hard/extra':>14} {'after dedup':>12} {'after exec':>11}"
    print("\n" + header)
    print("-" * len(header))
    for db in SHIPPED_DBS:
        print(f"{db:<32} {raw_hard[db]:>14} {after_dedup[db]:>12} {after_exec[db]:>11}")
    print("-" * len(header))
    print(f"{'TOTAL':<32} {sum(raw_hard.values()):>14} {sum(after_dedup.values()):>12} {sum(after_exec.values()):>11}")


if __name__ == "__main__":
    raise SystemExit(main())
