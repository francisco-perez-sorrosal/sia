"""Smoke tests for the SIA_EVAL_SET slice selector in the SQL-task grader.

These run the real evaluate.py against the real gold.json (offline, no model
calls) with an empty submission, and read the scored total from results.json:
unset/full scores every gold row; core48 scores only the tagged 48. A grep
guard asserts the framework (sia/) never names the slice -- the "no task-specific
logic in the loop" constraint.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_ROOT = REPO_ROOT / "hackathon_demo" / "sql_task"
EVALUATE_PY = TASK_ROOT / "data" / "public" / "evaluate.py"
GOLD_PATH = TASK_ROOT / "data" / "private" / "gold.json"
SIA_SRC = REPO_ROOT / "sia"


def _load_gold():
    with open(GOLD_PATH, encoding="utf-8") as f:
        return json.load(f)


def _score_total(tmp_path, eval_set):
    """Run evaluate.py with an empty submission and return total_questions scored."""
    submission = tmp_path / "responses.json"
    submission.write_text("[]", encoding="utf-8")
    output = tmp_path / "results.json"

    env = {"PATH": os.environ.get("PATH", "")}
    if eval_set is not None:
        env["SIA_EVAL_SET"] = eval_set

    subprocess.run(
        [sys.executable, str(EVALUATE_PY), "--submission", str(submission), "--output", str(output)],
        check=True,
        capture_output=True,
        env=env,
    )
    return json.loads(output.read_text(encoding="utf-8"))["total_questions"]


def test_full_scores_all_gold_rows(tmp_path):
    assert _score_total(tmp_path, "full") == len(_load_gold())


def test_unset_scores_all_gold_rows(tmp_path):
    assert _score_total(tmp_path, None) == len(_load_gold())


def test_core48_scores_exactly_the_tagged_rows(tmp_path):
    assert _score_total(tmp_path, "core48") == 48


def test_framework_source_names_no_task_slice():
    """sia/ must contain no SIA_EVAL_SET / core48 string (no task logic in the loop)."""
    offenders = []
    for path in SIA_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "SIA_EVAL_SET" in text or "core48" in text:
            offenders.append(str(path))
    assert offenders == [], f"task-slice strings leaked into framework: {offenders}"
