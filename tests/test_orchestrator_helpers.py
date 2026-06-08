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
    load_task_files,
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


# --- Task-family change library: load + meta-prompt injection -----------------


def _scaffold_task_dir(tmp_path, change_library=None):
    """Create a minimal task dir + shared dir matching TaskLayout for load_task_files."""
    task_dir = tmp_path / "task"
    (task_dir / "reference").mkdir(parents=True)
    (task_dir / "data" / "public").mkdir(parents=True)
    (task_dir / "reference" / "SAMPLE_TASK_DESCRIPTIONS.md").write_text("desc")
    (task_dir / "reference" / "reference_target_agent.py").write_text("def main():\n    pass\n")
    (task_dir / "data" / "public" / "task.md").write_text("task spec")
    if change_library is not None:
        (task_dir / "reference" / "change_library.md").write_text(change_library)

    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / "sample_agent_execution.json").write_text(json.dumps({"role": "user"}))
    return str(task_dir), str(shared_dir)


def test_load_task_files_populates_change_library_when_present(tmp_path):
    task_dir, shared_dir = _scaffold_task_dir(tmp_path, change_library="use temperature=0")

    task_files = load_task_files(task_dir, shared_dir)

    assert task_files.change_library == "use temperature=0"


def test_load_task_files_leaves_change_library_none_when_absent(tmp_path):
    task_dir, shared_dir = _scaffold_task_dir(tmp_path, change_library=None)

    task_files = load_task_files(task_dir, shared_dir)

    assert task_files.change_library is None


def _task_files_with_library(library):
    return TaskFiles(
        sample_task_descriptions="desc",
        reference_target_agent_py="agent",
        sample_agent_execution={"role": "user"},
        task_md="task",
        change_library=library,
    )


def test_meta_prompt_injects_change_library_block_when_enabled_and_present():
    prompt = build_meta_prompt(
        _task_files_with_library("DISTINCT-dedup rule pack"),
        "some-model",
        "/work",
        change_library_enabled=True,
    )

    assert "TASK-FAMILY CHANGE LIBRARY" in prompt
    assert "DISTINCT-dedup rule pack" in prompt


def test_meta_prompt_omits_change_library_block_when_disabled():
    """Knob off with a library present must reproduce the no-library baseline byte-for-byte."""
    baseline = build_meta_prompt(
        _task_files_with_library(None),
        "m",
        "/work",
        change_library_enabled=True,
    )
    knob_off = build_meta_prompt(
        _task_files_with_library("some library"),
        "m",
        "/work",
        change_library_enabled=False,
    )

    assert "TASK-FAMILY CHANGE LIBRARY" not in baseline
    assert knob_off == baseline


def test_meta_prompt_omits_change_library_block_when_absent():
    """Enabled but no library file → no injection, identical to the disabled path."""
    enabled_absent = build_meta_prompt(
        _task_files_with_library(None),
        "m",
        "/work",
        change_library_enabled=True,
    )
    default_off = build_meta_prompt(
        _task_files_with_library(None),
        "m",
        "/work",
    )

    assert "TASK-FAMILY CHANGE LIBRARY" not in enabled_absent
    assert enabled_absent == default_off


def test_meta_prompt_change_library_default_is_off():
    """A caller that does not pass change_library_enabled gets no injection."""
    prompt = build_meta_prompt(
        _task_files_with_library("present but not opted in"),
        "m",
        "/work",
    )

    assert "TASK-FAMILY CHANGE LIBRARY" not in prompt


def test_meta_prompt_composes_change_library_with_held_out_notice():
    """Both the change-library block (Lever C) and the held-out notice (Feature 2) present."""
    prompt = build_meta_prompt(
        _task_files_with_library("pattern pack"),
        "m",
        "/work",
        change_library_enabled=True,
    )

    assert "TASK-FAMILY CHANGE LIBRARY" in prompt
    assert HELD_OUT_GROUND_TRUTH_NOTICE in prompt


def test_meta_prompt_weights_mode_ignores_change_library():
    """Weights mode never injects the change library even when enabled+present."""
    prompt = build_meta_prompt(
        _task_files_with_library("pattern pack"),
        "m",
        "/work",
        focus="weights",
        change_library_enabled=True,
    )

    assert "TASK-FAMILY CHANGE LIBRARY" not in prompt
    assert "pattern pack" not in prompt


# --- Best-of-N candidate selection (select_best determinism / tiebreak) -------


def _candidate(name, accuracy, code_size):
    from sia.orchestrator import Candidate

    return Candidate(name=name, accuracy=accuracy, code_size=code_size)


def test_select_best_returns_highest_accuracy():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", 70.0, 100),
        _candidate("cand_1", 85.0, 200),
        _candidate("cand_2", 80.0, 50),
    ]
    assert select_best(candidates).name == "cand_1"


def test_select_best_breaks_accuracy_tie_with_smaller_code():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", 85.0, 300),
        _candidate("cand_1", 85.0, 120),
        _candidate("cand_2", 85.0, 250),
    ]
    assert select_best(candidates).name == "cand_1"


def test_select_best_full_tie_is_deterministic_by_order():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", 85.0, 100),
        _candidate("cand_1", 85.0, 100),
    ]
    # Identical accuracy AND code size -> first by list order wins.
    assert select_best(candidates).name == "cand_0"


def test_select_best_ranks_scored_above_none_accuracy():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", None, 50),
        _candidate("cand_1", 60.0, 400),
        _candidate("cand_2", None, 10),
    ]
    assert select_best(candidates).name == "cand_1"


def test_select_best_all_none_returns_first_without_crash():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", None, 200),
        _candidate("cand_1", None, 50),
    ]
    # No parseable accuracy anywhere -> defined fallback to the first candidate.
    assert select_best(candidates).name == "cand_0"


def test_select_best_single_candidate_returns_it():
    from sia.orchestrator import select_best

    only = _candidate("cand_0", 42.0, 999)
    assert select_best([only]).name == "cand_0"


def test_select_best_accepts_forward_compatible_minibatch_frac():
    from sia.orchestrator import select_best

    candidates = [
        _candidate("cand_0", 70.0, 100),
        _candidate("cand_1", 90.0, 100),
    ]
    # The signature reserves minibatch_frac for a later follow-up; passing it
    # must not change v1 full-eval argmax behavior.
    assert select_best(candidates, minibatch_frac=None).name == "cand_1"
