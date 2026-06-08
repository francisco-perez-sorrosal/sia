"""Unit tests for orchestrator helper functions."""

import inspect
import json
import re
from pathlib import Path

from sia.config import Config
from sia.orchestrator import (
    HELD_OUT_GROUND_TRUTH_NOTICE,
    TaskFiles,
    _build_eval_summary,
    _render_item,
    _select_failures,
    build_feedback_prompt,
    build_meta_prompt,
    load_agent_execution,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_TASK_ROOT = REPO_ROOT / "hackathon_demo" / "sql_task"


def _make_task_files():
    return TaskFiles(
        sample_task_descriptions="sample descriptions",
        reference_target_agent_py="def main():\n    pass\n",
        sample_agent_execution={"role": "user"},
        task_md="solve the task",
    )


def _build_feedback_prompt(tmp_path):
    return build_feedback_prompt(
        current_gen=1,
        max_gen=3,
        task_files=_make_task_files(),
        agent_py="print('agent')",
        task="task body",
        execution_status="SUCCESS",
        execution_section="execution details",
        run_dir=str(tmp_path),
        next_gen_dir=str(tmp_path / "gen_2"),
        previous_gens="None",
        task_model="claude-haiku-4-5",
    )


def test_load_single_trajectory(tmp_path):
    trajectory = [{"role": "user", "content": "hello"}]
    (tmp_path / "agent_execution.json").write_text(json.dumps(trajectory))

    data, is_multi = load_agent_execution(str(tmp_path))
    assert not is_multi
    assert isinstance(data, list)
    assert data[0]["role"] == "user"


def test_load_multi_trajectory(tmp_path):
    exec_dir = tmp_path / "agent_execution"
    exec_dir.mkdir()

    for i in range(3):
        traj = [{"role": "user", "content": f"question {i}"}]
        (exec_dir / f"execution_q{i}.json").write_text(json.dumps(traj))

    data, is_multi = load_agent_execution(str(tmp_path))
    assert is_multi
    assert data["count"] == 3
    assert len(data["trajectories"]) == 3


def test_load_missing_execution(tmp_path):
    data, _is_multi = load_agent_execution(str(tmp_path))
    assert "error" in data


def test_load_malformed_json(tmp_path):
    (tmp_path / "agent_execution.json").write_text("{not valid json")

    data, is_multi = load_agent_execution(str(tmp_path))
    assert not is_multi
    assert "error" in data or "raw_preview" in data


def test_load_empty_multi_trajectory_folder(tmp_path):
    (tmp_path / "agent_execution").mkdir()

    data, is_multi = load_agent_execution(str(tmp_path))
    assert is_multi
    assert "error" in data


# --- generic eval summary (task-agnostic items[] contract) -------------------


def _eval_data_with_items(items, results=None):
    data = {
        "accuracy_percent": 50.0,
        "correct": 1,
        "wrong_answer": 1,
        "exec_error": 0,
        "missing": 0,
        "items": items,
    }
    if results is not None:
        data["results"] = results
    return data


def test_eval_summary_ignores_results_and_never_leaks_gold():
    """The framework reads only items[]; a gold sentinel in results[] must not appear."""
    sentinel = "SELECT __GOLD_SENTINEL__"
    items = [
        {"id": "q0", "status": "CORRECT", "group": "g1"},
        {"id": "q1", "status": "WRONG", "group": "g1", "input": "ask", "output": "SELECT bad"},
    ]
    results = [
        {"id": "q0", "status": "CORRECT", "gold_sql": sentinel},
        {"id": "q1", "status": "WRONG", "gold_sql": sentinel},
    ]
    eval_data = _eval_data_with_items(items, results=results)

    summary = _build_eval_summary(eval_data, Config())

    assert sentinel not in summary
    assert "__GOLD_SENTINEL__" not in summary


def test_eval_summary_renders_only_failed_items_with_generic_fields():
    items = [
        {"id": "q0", "status": "CORRECT", "group": "g1"},
        {"id": "q1", "status": "WRONG", "group": "g1", "input": "ask", "output": "SELECT bad", "detail": "mismatch"},
        {"id": "q2", "status": "EXEC_ERROR", "group": "g2", "input": "ask2", "output": "SELECT x", "detail": "no col"},
    ]
    summary = _build_eval_summary(_eval_data_with_items(items), Config())

    # Failed items rendered; the passing item's id is absent from the failure block.
    assert "q1" in summary
    assert "q2" in summary
    assert "SELECT bad" in summary
    assert "no col" in summary
    # accuracy scalar header is present.
    assert "accuracy_percent: 50.0" in summary


def test_eval_summary_caps_and_stratifies_failures_across_status_and_group():
    cap = 4
    cfg = Config()
    cfg.FEEDBACK_FAILURE_SAMPLES = cap
    items = []
    # 6 WRONG in g1, 6 EXEC_ERROR in g2 -> selection must spread across both.
    for i in range(6):
        items.append({"id": f"w{i}", "status": "WRONG", "group": "g1"})
    for i in range(6):
        items.append({"id": f"e{i}", "status": "EXEC_ERROR", "group": "g2"})

    failures = _select_failures(items, cfg.VERIFIER_PASS_STATUSES, cap)

    assert len(failures) == cap
    statuses = {f["status"] for f in failures}
    assert statuses == {"WRONG", "EXEC_ERROR"}  # stratified, not all from one bucket


def test_eval_summary_graceful_when_no_items():
    summary = _build_eval_summary({"accuracy_percent": 100.0}, Config())
    assert "No failed held-out items" in summary


def test_render_item_projects_only_generic_whitelist():
    item = {
        "id": "q1",
        "status": "WRONG",
        "group": "g1",
        "category": "wrong_answer",
        "input": "ask",
        "output": "SELECT bad",
        "detail": "mismatch",
        "gold_sql": "SELECT secret",  # must be dropped
        "db_id": "g1",  # task-specific key, must be dropped
    }
    rendered = _render_item(item)
    assert set(rendered.keys()) == {"id", "status", "group", "category", "input", "output", "detail"}
    assert "gold_sql" not in rendered
    assert "db_id" not in rendered


def test_eval_summary_source_has_no_task_specific_identifiers():
    """Genericity guard: the sia/ eval-summary code path must read only generic keys."""
    forbidden = ("gold", "db_id", "candidate_sql", "sample.json", "held_out")
    for fn in (_build_eval_summary, _select_failures, _render_item):
        src = inspect.getsource(fn).lower()
        for token in forbidden:
            assert token not in src, f"{fn.__name__} references task-specific identifier {token!r}"
        # `sql` as a standalone word must not appear (substring guard tolerates 'results').
        assert not re.search(r"\bsql\b", src), f"{fn.__name__} references 'sql'"


# --- Held-out ground-truth seal: Layer 2 prompt instruction -------------------


def test_meta_prompt_carries_held_out_notice():
    prompt = build_meta_prompt(_make_task_files(), "claude-haiku-4-5", "/tmp/wd")
    assert HELD_OUT_GROUND_TRUTH_NOTICE in prompt


def test_feedback_prompt_carries_held_out_notice(tmp_path):
    prompt = _build_feedback_prompt(tmp_path)
    assert HELD_OUT_GROUND_TRUTH_NOTICE in prompt


def test_held_out_notice_names_no_concrete_private_path():
    """The generic notice must not hardcode any task's private directory path."""
    assert "data/private" not in HELD_OUT_GROUND_TRUTH_NOTICE


# --- Layer 2 task-content cleanup: no private-path pointers in shipped task content --


def test_sql_task_content_files_do_not_reveal_private_path():
    """task.md and SAMPLE_TASK_DESCRIPTIONS.md must not point the agent at data/private."""
    task_md = (SQL_TASK_ROOT / "data" / "public" / "task.md").read_text(encoding="utf-8")
    sample_desc = (SQL_TASK_ROOT / "reference" / "SAMPLE_TASK_DESCRIPTIONS.md").read_text(encoding="utf-8")
    assert "data/private" not in task_md
    assert "data/private" not in sample_desc
