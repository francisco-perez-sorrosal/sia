"""The gpqa grader must write results.json so the orchestrator picks it up.

The orchestrator (sia/orchestrator.py) invokes each task's evaluate.py with only
``--gen-dir`` and then reads ``gen-dir/results.json`` (Names.RESULTS_JSON). A grader
that defaults its output to any other filename produces no results.json, so the run
is silently scored as a "warning" and never feeds the feedback loop. This locks the
gpqa grader to that contract, matching the lawbench and longcot-chess graders.
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).parent.parent
GPQA_DIR = REPO_ROOT / "sia" / "tasks" / "gpqa"
EVALUATE_PY = GPQA_DIR / "data" / "public" / "evaluate.py"
PRIVATE_QUESTIONS = GPQA_DIR / "data" / "private" / "diamond_questions.json"


def _wrong_letter(correct_letter: str) -> str:
    """Return any A-D letter that differs from the correct one."""
    return next(letter for letter in "ABCD" if letter != correct_letter)


@pytest.fixture
def gpqa_gen_dir(tmp_path: Path) -> SimpleNamespace:
    """A generation dir holding a submission under results/, as the agent writes it.

    Answers the first three questions (two correct, one wrong) so the test can assert
    the grader actually scored this submission rather than emitting an empty result.
    """
    questions = json.loads(PRIVATE_QUESTIONS.read_text(encoding="utf-8"))
    sample = questions[:3]
    details = [
        {"question_id": sample[0]["id"], "model_answer": sample[0]["correct_answer_letter"]},
        {"question_id": sample[1]["id"], "model_answer": sample[1]["correct_answer_letter"]},
        {"question_id": sample[2]["id"], "model_answer": _wrong_letter(sample[2]["correct_answer_letter"])},
    ]

    results_subdir = tmp_path / "results"
    results_subdir.mkdir()
    (results_subdir / "submission.json").write_text(json.dumps({"details": details}), encoding="utf-8")

    return SimpleNamespace(path=tmp_path, expected_correct=2, expected_incorrect=1)


def test_gpqa_evaluate_writes_results_json_under_gen_dir(gpqa_gen_dir: SimpleNamespace):
    gen_dir = gpqa_gen_dir.path
    result = subprocess.run(
        [sys.executable, str(EVALUATE_PY), "--gen-dir", str(gen_dir)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"grader failed: {result.stdout}\n{result.stderr}"

    results_json = gen_dir / "results.json"
    assert results_json.is_file(), "grader did not write the orchestrator-expected results.json"
    assert not (gen_dir / "evaluation_results.json").exists(), "grader wrote the legacy filename"

    scored = json.loads(results_json.read_text(encoding="utf-8"))
    assert scored["correct"] == gpqa_gen_dir.expected_correct
    assert scored["incorrect"] == gpqa_gen_dir.expected_incorrect
    assert "accuracy" in scored
