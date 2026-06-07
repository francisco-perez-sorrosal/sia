"""Unit tests for the multi-DB NL->SQL execution grader (data/public/evaluate.py).

The grader is a standalone script inside the task folder (which is excluded from
lint/type-check like sia/tasks/), so it is loaded here by file path via importlib
rather than imported as a package module.

The grader is MULTI-DATABASE: each gold item carries a db_id naming which
data/public/<db_id>.sqlite the candidate query is executed against. These tests
build gold fixtures spanning >=2 db_ids to lock in the per-question routing.

The load-bearing assertion across these tests is the UNITS contract: top-level
`accuracy` is a FRACTION in [0.0, 1.0] (a 1-of-2 partial is 0.5, never 50.0).
This is the regression guard against the 100x-off trap inherited from the
longcot-chess template the grader was cloned from.
"""

import importlib.util
from pathlib import Path

import pytest

_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "sql_task" / "data" / "public"
_GRADER_PATH = _PUBLIC_DIR / "evaluate.py"

# Two distinct databases in the task's public dir, exercised by every test below
# so the per-question db_id routing is always under test.
_DB_A = "concert_singer"
_DB_B = "world_1"


def _load_grader():
    spec = importlib.util.spec_from_file_location("sql_grader", _GRADER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grader():
    return _load_grader()


def _evaluate(grader, submission, gold):
    # Route by db_id against the per-db_id SQLite files in the public dir.
    return grader.evaluate_submission(submission, gold, _PUBLIC_DIR)


def test_all_correct_scores_fraction_one(grader):
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [
        {"question_id": "q1", "sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "sql": "SELECT count(*) FROM country"},
    ]
    result = _evaluate(grader, submission, gold)
    assert result["accuracy"] == 1.0
    assert 0.0 <= result["accuracy"] <= 1.0
    assert result["accuracy_percent"] == 100.0
    assert result["correct"] == 2


def test_all_wrong_scores_fraction_zero(grader):
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [
        # Both return result sets that differ from gold.
        {"question_id": "q1", "sql": "SELECT count(*) FROM stadium"},
        {"question_id": "q2", "sql": "SELECT count(*) FROM city"},
    ]
    result = _evaluate(grader, submission, gold)
    assert result["accuracy"] == 0.0
    assert 0.0 <= result["accuracy"] <= 1.0
    assert result["correct"] == 0


def test_partial_one_of_two_is_half_not_fifty(grader):
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [
        {"question_id": "q1", "sql": "SELECT count(*) FROM singer"},  # correct
        {"question_id": "q2", "sql": "SELECT count(*) FROM city"},  # wrong
    ]
    result = _evaluate(grader, submission, gold)
    # The whole point of this test: the fraction unit. 1-of-2 -> 0.5, NEVER 50.0.
    assert result["accuracy"] == 0.5
    assert result["accuracy_percent"] == 50.0
    assert 0.0 <= result["accuracy"] <= 1.0


def test_candidate_execution_error_contributes_zero(grader):
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [
        {"question_id": "q1", "sql": "SELECT count(*) FROM singer"},  # correct
        {"question_id": "q2", "sql": "SELECT * FROM NoSuchTable"},  # exec error -> 0
    ]
    result = _evaluate(grader, submission, gold)
    assert result["accuracy"] == 0.5
    assert result["exec_error"] == 1
    statuses = {d["question_id"]: d["status"] for d in result["details"]}
    assert statuses["q2"] == "EXEC_ERROR"


def test_routes_each_question_to_its_own_database(grader):
    # `singer` exists only in concert_singer; `country` exists only in world_1.
    # If routing were wrong (both run against one db), one query would EXEC_ERROR.
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [
        {"question_id": "q1", "sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "sql": "SELECT count(*) FROM country"},
    ]
    result = _evaluate(grader, submission, gold)
    assert result["exec_error"] == 0
    assert result["correct"] == 2
    db_by_id = {d["question_id"]: d["db_id"] for d in result["details"]}
    assert db_by_id["q1"] == _DB_A
    assert db_by_id["q2"] == _DB_B


def test_sorted_multiset_ignores_row_and_column_order(grader):
    gold = [
        {
            "question_id": "q1",
            "db_id": _DB_B,
            "gold_sql": "SELECT Name, Population FROM country WHERE Continent = 'Asia'",
        }
    ]
    submission = [
        {
            # Columns swapped (Population, Name) and rows ordered differently:
            # sorted-multiset comparison must still mark this correct.
            "question_id": "q1",
            "sql": "SELECT Population, Name FROM country WHERE Continent = 'Asia' ORDER BY Name DESC",
        }
    ]
    result = _evaluate(grader, submission, gold)
    assert result["accuracy"] == 1.0
    assert result["details"][0]["status"] == "CORRECT"


def test_missing_submission_item_contributes_zero(grader):
    gold = [
        {"question_id": "q1", "db_id": _DB_A, "gold_sql": "SELECT count(*) FROM singer"},
        {"question_id": "q2", "db_id": _DB_B, "gold_sql": "SELECT count(*) FROM country"},
    ]
    submission = [{"question_id": "q1", "sql": "SELECT count(*) FROM singer"}]  # q2 absent
    result = _evaluate(grader, submission, gold)
    assert result["accuracy"] == 0.5
    assert result["missing"] == 1
