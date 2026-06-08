"""Offline integrity harness for the expanded SQL-task held-out eval set.

No model calls. These tests lock down the build invariants for the 220-row
gold set: every gold SQL executes and returns rows, no few-shot demo leaked
into the held-out set, the original 48 are preserved verbatim, the MANIFEST
sha256 binds the canonical gold.json, and the public held_out matches the gold
question set with no gold leaked.

The DB-execution test skips cleanly when a .sqlite is missing so a data-less
checkout still passes structurally.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_ROOT = REPO_ROOT / "hackathon_demo" / "sql_task"
GOLD_PATH = TASK_ROOT / "data" / "private" / "gold.json"
CORE_PATH = TASK_ROOT / "data" / "private" / "gold_core48.json"
MANIFEST_PATH = TASK_ROOT / "data" / "private" / "MANIFEST.json"
SAMPLE_PATH = TASK_ROOT / "data" / "public" / "sample.json"
PUBLIC_DIR = TASK_ROOT / "data" / "public"
CORE48_IDS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "core48_ids.txt"

EXPECTED_CORE_COUNT = 48
ALLOWED_HARDNESS = {"hard", "extra"}


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize(question, db_id, gold_sql):
    """Whitespace-collapsed key for matching a (question, db, sql) triple."""
    return tuple(" ".join(str(part).split()).lower() for part in (question, db_id, gold_sql))


def test_every_gold_sql_executes_and_returns_rows():
    gold = _load(GOLD_PATH)
    failures = []
    for q in gold:
        db_path = PUBLIC_DIR / f"{q['db_id']}.sqlite"
        if not db_path.is_file():
            pytest.skip(f"Missing database {db_path.name}; skipping execution invariant on a data-less checkout")
        uri = f"file:{db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            rows = conn.execute(q["gold_sql"]).fetchall()
            conn.close()
        except sqlite3.Error as e:
            failures.append(f"{q['question_id']}: execution error: {e}")
            continue
        if not rows:
            failures.append(f"{q['question_id']}: empty result set")
    assert failures == [], "gold SQL build invariant violated:\n" + "\n".join(failures)


def test_no_few_shot_demo_leaked_into_held_out():
    gold = _load(GOLD_PATH)
    sample = _load(SAMPLE_PATH)
    gold_keys = {_normalize(q["question"], q["db_id"], q["gold_sql"]) for q in gold}
    leaked = [fs for fs in sample["few_shot"] if _normalize(fs["question"], fs["db_id"], fs["gold_sql"]) in gold_keys]
    assert leaked == [], f"few-shot demo leaked into held-out gold: {leaked}"


def test_core48_rows_preserved_verbatim():
    gold = _load(GOLD_PATH)
    core48_source = _load(CORE_PATH)
    expected_ids = CORE48_IDS_FIXTURE.read_text(encoding="utf-8").split()

    core_rows = [q for q in gold if q.get("core")]
    assert len(core_rows) == EXPECTED_CORE_COUNT
    assert [q["question_id"] for q in core_rows] == expected_ids

    # Byte-faithful: each core row equals its pristine source row plus the core tag.
    for got, src in zip(core_rows, core48_source, strict=True):
        assert {k: v for k, v in got.items() if k != "core"} == src


def test_manifest_sha256_binds_gold():
    manifest = _load(MANIFEST_PATH)
    fresh = hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
    assert manifest["sha256"] == fresh
    assert manifest["question_count"] == len(_load(GOLD_PATH))


def test_held_out_matches_gold_question_set_without_gold():
    gold = _load(GOLD_PATH)
    held_out = _load(SAMPLE_PATH)["held_out"]
    assert [q["question_id"] for q in held_out] == [q["question_id"] for q in gold]
    assert not any("gold_sql" in q for q in held_out)


def test_gold_hardness_and_db_spread():
    gold = _load(GOLD_PATH)
    assert {q["hardness"] for q in gold} <= ALLOWED_HARDNESS
    assert len({q["db_id"] for q in gold}) == 6
