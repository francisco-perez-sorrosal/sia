"""Integration tests for generation loop with mocked agents."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sia.config import Config
from sia.context_manager import ContextManager
from sia.orchestrator import (
    RunSetup,
    TaskFiles,
    _run_target_agent,
    run_generation,
)
from sia.profiles import load_meta_agent_profile, load_target_agent_profile

DEFAULT_META_PROFILE = load_meta_agent_profile("default-meta")
DEFAULT_TARGET_PROFILE = load_target_agent_profile("default-target")
DEFAULT_TASK_MODEL = DEFAULT_TARGET_PROFILE.model
DEFAULT_TARGET_PROVIDER = DEFAULT_TARGET_PROFILE.provider


def _make_task_files(tmp_path):
    """Create minimal task structure with all required files."""
    task_dir = tmp_path / "task"
    shared_dir = task_dir / "_shared"
    ref_dir = task_dir / "reference"
    pub_dir = task_dir / "data" / "public"

    for d in (shared_dir, ref_dir, pub_dir):
        d.mkdir(parents=True)

    (ref_dir / "SAMPLE_TASK_DESCRIPTIONS.md").write_text("Sample task description text.")
    (ref_dir / "reference_target_agent.py").write_text("print('ref agent')")
    (shared_dir / "sample_agent_execution.json").write_text(json.dumps([{"role": "user"}]))
    (pub_dir / "task.md").write_text("# Test task\nSolve the problem.")
    return task_dir, shared_dir


def _make_run_setup(tmp_path, task_dir):
    """Create a RunSetup with initialized context manager."""
    run_dir = tmp_path / "runs" / "run_1"
    gen1 = run_dir / "gen_1"
    gen1.mkdir(parents=True)
    (gen1 / "target_agent.py").write_text("print('agent')\n")

    context_mgr = ContextManager(
        str(run_dir),
        {
            "task_dir": str(task_dir),
            "meta_model": "haiku",
            "task_model": "haiku",
            "agent_impl": "claude",
            "max_gen": 1,
        },
    )
    context_mgr.initialize()

    return RunSetup(
        run_directory=str(run_dir),
        meta_agent_working_directory=str(gen1),
        venv_dir=str(tmp_path / "venv"),
        context_mgr=context_mgr,
    )


@patch("sia.orchestrator.subprocess.Popen")
def test_run_target_agent_success(mock_popen_cls, tmp_path):
    """_run_target_agent with sandbox=none uses standard Popen path."""
    gen_dir = tmp_path / "gen_1"
    gen_dir.mkdir()
    stdout_log = str(gen_dir / "stdout.log")
    (gen_dir / "target_agent.py").write_text("print('ok')")

    # Mock Popen to simulate a process that writes one line then exits 0
    mock_process = MagicMock()
    mock_process.stdout = iter(["line1\n"])
    mock_process.wait.return_value = 0
    mock_popen_cls.return_value = mock_process

    success, _stdout, _stderr, err = _run_target_agent(
        venv_dir="/fake/venv",
        target_agent_path=str(gen_dir / "target_agent.py"),
        abs_dataset_dir="/data",
        gen_dir=str(gen_dir),
        stdout_log_file=stdout_log,
        sandbox="none",
        env_config=Config(),
    )

    assert success is True
    assert err == ""
    mock_popen_cls.assert_called_once()
    # Verify no Docker args in the command
    cmd = mock_popen_cls.call_args[0][0]
    assert "docker" not in cmd


@patch("sia.orchestrator.subprocess.Popen")
def test_run_target_agent_failure(mock_popen_cls, tmp_path):
    """_run_target_agent returns (False, ...) on non-zero exit."""
    gen_dir = tmp_path / "gen_1"
    gen_dir.mkdir()
    stdout_log = str(gen_dir / "stdout.log")
    (gen_dir / "target_agent.py").write_text("raise SystemExit(1)")

    mock_process = MagicMock()
    mock_process.stdout = iter(["error\n"])
    mock_process.wait.return_value = 1
    mock_popen_cls.return_value = mock_process

    success, _stdout, _stderr, err = _run_target_agent(
        venv_dir="/fake/venv",
        target_agent_path=str(gen_dir / "target_agent.py"),
        abs_dataset_dir="/data",
        gen_dir=str(gen_dir),
        stdout_log_file=stdout_log,
        sandbox="none",
        env_config=Config(),
    )

    assert success is False
    assert "exit code 1" in err


@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_single_generation_creates_context(mock_run_ta, mock_run_fb, tmp_path):
    """run_generation with max_gen=1 creates context.md entry."""
    task_dir, _shared_dir = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    mock_run_ta.return_value = (True, "output", "", "")

    task_files = TaskFiles(
        sample_task_descriptions="desc",
        reference_target_agent_py="ref",
        sample_agent_execution={},
        task_md="# Task",
    )

    run_generation(
        current_gen=1,
        max_gen=1,
        run_setup=run_setup,
        task_files=task_files,
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=Config(),
        task_model=DEFAULT_TARGET_PROFILE.model,
        target_provider=DEFAULT_TARGET_PROFILE.provider,
    )

    # Verify context.md was updated
    ctx = (Path(run_setup.run_directory) / "context.md").read_text()
    assert "Generation 1" in ctx
    assert "SUCCESS" in ctx

    # Feedback agent should NOT be called (last generation)
    mock_run_fb.assert_not_called()


@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_run_generation_directory_structure(mock_run_ta, mock_run_fb, tmp_path):
    """Verify gen directory structure is preserved after run."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    mock_run_ta.return_value = (True, "output", "", "")

    run_generation(
        current_gen=1,
        max_gen=1,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir="/data",
        dataset_dir="/data",
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=Config(),
        task_model=DEFAULT_TARGET_PROFILE.model,
        target_provider=DEFAULT_TARGET_PROFILE.provider,
    )

    gen_dir = Path(run_setup.run_directory) / "gen_1"
    assert gen_dir.is_dir()
    assert (gen_dir / "target_agent.py").is_file()


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_two_generations_with_feedback(mock_run_ta, mock_run_fb, mock_llm, tmp_path):
    """Two-generation evolution: feedback agent called for gen_1, skipped for gen_2."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    mock_run_ta.return_value = (True, "output", "", "")

    # Stub _run_feedback_agent to create gen_2/target_agent.py
    def _fake_feedback(*args, **kwargs):
        next_gen_dir = Path(run_setup.run_directory) / "gen_2"
        next_gen_dir.mkdir(exist_ok=True)
        (next_gen_dir / "target_agent.py").write_text("print('improved')\n")
        (next_gen_dir / "improvement.md").write_text("- Better prompts\n- More robust error handling\n")

    mock_run_fb.side_effect = _fake_feedback

    task_files = TaskFiles("d", "r", {}, "# T")

    # Generation 1 (should trigger feedback agent)
    run_generation(
        current_gen=1,
        max_gen=2,
        run_setup=run_setup,
        task_files=task_files,
        abs_dataset_dir="/data",
        dataset_dir="/data",
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=Config(),
        task_model=DEFAULT_TARGET_PROFILE.model,
        target_provider=DEFAULT_TARGET_PROFILE.provider,
    )
    mock_run_fb.assert_called_once()

    # Generation 2 (should NOT trigger feedback agent -- last generation)
    run_generation(
        current_gen=2,
        max_gen=2,
        run_setup=run_setup,
        task_files=task_files,
        abs_dataset_dir="/data",
        dataset_dir="/data",
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=Config(),
        task_model=DEFAULT_TARGET_PROFILE.model,
        target_provider=DEFAULT_TARGET_PROFILE.provider,
    )
    assert mock_run_fb.call_count == 1  # still only called once

    # Verify both gen directories exist
    run_dir = Path(run_setup.run_directory)
    assert (run_dir / "gen_1" / "target_agent.py").is_file()
    assert (run_dir / "gen_2" / "target_agent.py").is_file()

    # Verify context.md tracks both generations
    ctx = (run_dir / "context.md").read_text()
    assert "Generation 1" in ctx
    assert "Generation 2" in ctx

    # Verify finalize produces summary
    run_setup.context_mgr.finalize()
    ctx_final = (run_dir / "context.md").read_text()
    assert "Summary Statistics" in ctx_final
    assert "**Total Generations**: 2" in ctx_final


# --- base-on-best / reject-regression helpers --------------------------------


def _ctx_with_generations(accuracies):
    """A ContextManager-like stub whose generations carry the given accuracies."""
    mgr = ContextManager.__new__(ContextManager)
    mgr.generations = [
        {
            "gen_num": i + 1,
            "agent_stats": {"size": 100, "lines": 10},
            "metrics": {"accuracy": acc},
            "success": True,
        }
        for i, acc in enumerate(accuracies)
    ]
    return mgr


def test_select_base_generation_picks_best_when_enabled():
    from sia.orchestrator import _select_base_generation

    ctx = _ctx_with_generations([80, 90, 85])
    assert _select_base_generation(ctx, current_gen=3, base_on_best=True) == 2


def test_select_base_generation_uses_current_when_disabled():
    from sia.orchestrator import _select_base_generation

    ctx = _ctx_with_generations([80, 90, 85])
    assert _select_base_generation(ctx, current_gen=3, base_on_best=False) == 3


def test_select_base_generation_falls_back_to_current_without_accuracy():
    from sia.orchestrator import _select_base_generation

    ctx = _ctx_with_generations([None, None])
    assert _select_base_generation(ctx, current_gen=2, base_on_best=True) == 2


def test_is_regression_true_when_below_earlier_best():
    from sia.orchestrator import _is_regression

    ctx = _ctx_with_generations([90, 80])
    assert _is_regression(ctx, current_gen=2) is True


def test_is_regression_false_when_improving():
    from sia.orchestrator import _is_regression

    ctx = _ctx_with_generations([80, 90])
    assert _is_regression(ctx, current_gen=2) is False


def test_is_regression_false_for_first_generation():
    from sia.orchestrator import _is_regression

    ctx = _ctx_with_generations([85])
    assert _is_regression(ctx, current_gen=1) is False


def _write_results_json(gen_dir, accuracy):
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "results.json").write_text(json.dumps({"accuracy": accuracy}))


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_reject_regression_reprompts_once_on_regressor(mock_run_ta, mock_run_fb, mock_llm, tmp_path):
    """A regressing generation triggers one extra feedback attempt when the knob is on."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    # Seed a strong gen_1 in context so gen_2 (weaker) is a regressor.
    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 90.0}, "success": True}
    ]

    mock_run_ta.return_value = (True, "output", "", "")
    # gen_2 evaluates to a lower accuracy than gen_1.
    _write_results_json(Path(run_setup.run_directory) / "gen_2", 70.0)

    cfg = Config()
    cfg.REJECT_REGRESSION = True
    cfg.REGRESSION_REPROMPT_MAX = 1
    cfg.BASE_ON_BEST = True

    run_generation(
        current_gen=2,
        max_gen=3,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir="/data",
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # Base attempt + one reject-regression re-prompt.
    assert mock_run_fb.call_count == 2


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_no_reprompt_for_non_regressor(mock_run_ta, mock_run_fb, mock_llm, tmp_path):
    """An improving generation runs the feedback agent exactly once."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 70.0}, "success": True}
    ]

    mock_run_ta.return_value = (True, "output", "", "")
    _write_results_json(Path(run_setup.run_directory) / "gen_2", 90.0)

    cfg = Config()
    cfg.REJECT_REGRESSION = True

    run_generation(
        current_gen=2,
        max_gen=3,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir="/data",
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    assert mock_run_fb.call_count == 1


# --- Best-of-N at gen-1 (meta-agent candidates) ----------------------------


def _writing_run_agent(write_fn):
    """An async run_agent stub that writes a target_agent.py into the working dir.

    `asyncio.run(run_agent(...))` awaits this coroutine, so the candidate scaffold
    exists before _run_target_agent / run_evaluation run on the dir.
    """

    async def _run_agent(
        *, model_name, max_turns, prompt, agent_working_directory, agent_impl, provider=None, protected_paths=None
    ):
        write_fn(agent_working_directory)

    return _run_agent


def _make_gen1_task_files():
    return TaskFiles(
        sample_task_descriptions="desc",
        reference_target_agent_py="print('ref')",
        sample_agent_execution={},
        task_md="# Task",
    )


def _candidate_eval_factory(accuracies):
    """A thread-safe run_evaluation stub that writes results.json into each candidate dir.

    Each call writes the next accuracy from `accuracies` under a lock (so concurrent
    candidate threads cannot race the shared counter). Order-based: use only where the
    test asserts on counts/winner-by-value, not winner-by-specific-candidate.
    """
    calls = {"i": 0}
    lock = threading.Lock()

    def _fake_eval(gen_directory, task_dir, venv_dir, config=None):
        with lock:
            idx = calls["i"]
            calls["i"] += 1
        acc = accuracies[idx] if idx < len(accuracies) else 0.0
        Path(gen_directory, "results.json").write_text(json.dumps({"accuracy": acc}))
        return {"status": "success"}

    return _fake_eval


def _cand_dir_eval_factory(by_name, default=50.0):
    """A run_evaluation stub keyed by candidate dir name (e.g. {"cand_1": 85.0}).

    Robust to non-candidate eval calls (e.g. the current generation's own evaluation),
    which simply receive the default accuracy and never disturb candidate ordering.
    """

    def _fake_eval(gen_directory, task_dir, venv_dir, config=None):
        acc = by_name.get(Path(gen_directory).name, default)
        Path(gen_directory, "results.json").write_text(json.dumps({"accuracy": acc}))
        return {"status": "success"}

    return _fake_eval


@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_gen1_best_of_n_produces_candidates_and_promotes_winner(mock_run_agent, mock_run_ta, mock_eval, tmp_path):
    """K=3 meta candidates produce 3 cand dirs; the highest-accuracy one is promoted."""
    from sia.orchestrator import _run_gen1_best_of_n

    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    def _write(working_dir):
        marker = Path(working_dir).name
        Path(working_dir, "target_agent.py").write_text(f"# {marker}\nprint('a')\n")

    mock_run_agent.side_effect = _writing_run_agent(_write)
    mock_run_ta.return_value = (True, "out", "", "")
    # Dir-keyed (order-independent) so the winner is deterministic under concurrency.
    mock_eval.side_effect = _cand_dir_eval_factory({"cand_0": 70.0, "cand_1": 85.0, "cand_2": 80.0})

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests

    _run_gen1_best_of_n(
        run_setup=run_setup,
        task_files=_make_gen1_task_files(),
        task_model=DEFAULT_TASK_MODEL,
        meta_profile=DEFAULT_META_PROFILE,
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        sandbox="none",
        env_config=cfg,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    gen1 = Path(run_setup.run_directory) / "gen_1"
    assert (gen1 / "cand_0").is_dir()
    assert (gen1 / "cand_1").is_dir()
    assert (gen1 / "cand_2").is_dir()
    # The winner (cand_1, accuracy 85) is promoted to gen_1/.
    promoted_results = json.loads((gen1 / "results.json").read_text())
    assert promoted_results["accuracy"] == 85.0
    assert (gen1 / "target_agent.py").read_text() == (gen1 / "cand_1" / "target_agent.py").read_text()


@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_gen1_best_of_n_runs_k_candidates(mock_run_agent, mock_run_ta, mock_eval, tmp_path):
    """K=3 invokes the meta-agent and the candidate target run exactly K times each."""
    from sia.orchestrator import _run_gen1_best_of_n

    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    mock_run_agent.side_effect = _writing_run_agent(lambda d: Path(d, "target_agent.py").write_text("print('a')\n"))
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([70.0, 75.0, 72.0])

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests

    _run_gen1_best_of_n(
        run_setup=run_setup,
        task_files=_make_gen1_task_files(),
        task_model=DEFAULT_TASK_MODEL,
        meta_profile=DEFAULT_META_PROFILE,
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        sandbox="none",
        env_config=cfg,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    assert mock_run_agent.call_count == 3
    assert mock_run_ta.call_count == 3
    assert mock_eval.call_count == 3


# --- Best-of-N at gen>=2 (feedback-agent candidates) -----------------------


def _seed_gen1_with_accuracy(run_setup, accuracy):
    """Seed a scored gen_1 in context + on disk so gen_2 can evolve from it."""
    run_setup.context_mgr.generations = [
        {
            "gen_num": 1,
            "agent_stats": {"size": 100, "lines": 10},
            "metrics": {"accuracy": accuracy},
            "success": True,
        }
    ]
    gen1 = Path(run_setup.run_directory) / "gen_1"
    gen1.mkdir(parents=True, exist_ok=True)
    (gen1 / "target_agent.py").write_text("print('gen1 agent')\n")


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_gen_n_best_of_n_produces_candidates_and_promotes_winner(
    mock_run_agent, mock_run_ta, mock_eval, mock_llm, tmp_path
):
    """K=3 feedback candidates produce 3 cand dirs; the highest-accuracy one is promoted."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    _seed_gen1_with_accuracy(run_setup, 80.0)

    def _write(working_dir):
        marker = Path(working_dir).name
        Path(working_dir, "target_agent.py").write_text(f"# {marker}\nprint('a')\n")

    mock_run_agent.side_effect = _writing_run_agent(_write)
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _cand_dir_eval_factory({"cand_0": 70.0, "cand_1": 85.0, "cand_2": 80.0})

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests

    run_generation(
        current_gen=1,
        max_gen=2,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    gen2 = Path(run_setup.run_directory) / "gen_2"
    assert (gen2 / "cand_0").is_dir()
    assert (gen2 / "cand_1").is_dir()
    assert (gen2 / "cand_2").is_dir()
    # The winner (cand_1, accuracy 85) is promoted to gen_2/.
    promoted_results = json.loads((gen2 / "results.json").read_text())
    assert promoted_results["accuracy"] == 85.0
    assert (gen2 / "target_agent.py").read_text() == (gen2 / "cand_1" / "target_agent.py").read_text()


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_gen_n_best_of_n_subsumes_reject_regression_reprompt(
    mock_run_agent, mock_run_ta, mock_eval, mock_llm, tmp_path
):
    """With K=3 and a regressing current gen, the feedback agent runs exactly K times.

    Selecting the best of K already rejects a bad draw, so the reject-regression re-prompt
    must NOT additionally fire (no K+reprompt).
    """
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    # Strong gen_1 then a weaker current gen_2 makes gen_2 a regressor.
    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 90.0}, "success": True}
    ]
    gen1 = Path(run_setup.run_directory) / "gen_1"
    gen1.mkdir(parents=True, exist_ok=True)
    (gen1 / "target_agent.py").write_text("print('gen1 agent')\n")
    _write_results_json(Path(run_setup.run_directory) / "gen_2", 70.0)

    mock_run_agent.side_effect = _writing_run_agent(lambda d: Path(d, "target_agent.py").write_text("print('cand')\n"))
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([60.0, 65.0, 62.0])

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests
    cfg.REJECT_REGRESSION = True
    cfg.REGRESSION_REPROMPT_MAX = 1

    run_generation(
        current_gen=2,
        max_gen=3,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # Exactly K feedback runs (one run_agent per candidate), no extra re-prompt.
    assert mock_run_agent.call_count == 3


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator._run_feedback_agent")
def test_gen_n_best_of_n_passes_base_on_best_to_each_candidate(mock_run_fb, mock_run_ta, mock_eval, mock_llm, tmp_path):
    """Each feedback candidate receives the base-on-best generation as its base_gen."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    # gen_1=90 (best), gen_2=70, gen_3 is current and weaker => base-on-best picks gen_1.
    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 90.0}, "success": True},
        {"gen_num": 2, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 70.0}, "success": True},
    ]
    for g in (1, 2):
        d = Path(run_setup.run_directory) / f"gen_{g}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "target_agent.py").write_text(f"print('gen{g}')\n")
    _write_results_json(Path(run_setup.run_directory) / "gen_3", 60.0)

    def _fb_writes(*args, **kwargs):
        Path(kwargs["next_gen_dir"], "target_agent.py").write_text("print('cand')\n")

    mock_run_fb.side_effect = _fb_writes
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([60.0, 65.0, 62.0])

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests
    cfg.BASE_ON_BEST = True

    run_generation(
        current_gen=3,
        max_gen=4,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    assert mock_run_fb.call_count == 3
    for call in mock_run_fb.call_args_list:
        assert call.kwargs["base_gen"] == 1


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator._run_feedback_agent")
@patch("sia.orchestrator._run_target_agent")
def test_gen_n_k1_takes_todays_path_without_candidate_dirs(mock_run_ta, mock_run_fb, mock_llm, tmp_path):
    """With BEST_OF_N=1, gen>=2 runs the single-feedback path and creates no cand dirs."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    _seed_gen1_with_accuracy(run_setup, 80.0)

    def _fake_feedback(*args, **kwargs):
        next_gen_dir = Path(kwargs["next_gen_dir"])
        next_gen_dir.mkdir(parents=True, exist_ok=True)
        (next_gen_dir / "target_agent.py").write_text("print('improved')\n")

    mock_run_fb.side_effect = _fake_feedback
    mock_run_ta.return_value = (True, "out", "", "")

    cfg = Config()
    cfg.BEST_OF_N = 1

    run_generation(
        current_gen=1,
        max_gen=2,
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    gen2 = Path(run_setup.run_directory) / "gen_2"
    assert mock_run_fb.call_count == 1
    assert not (gen2 / "cand_0").exists()


# --- Best-of-N promoted-generation reuse (no re-run / no re-eval) -----------


def _seed_promoted_generation(run_setup, gen_num, accuracy):
    """Simulate a best-of-N-promoted generation already produced + evaluated on disk."""
    gen_dir = Path(run_setup.run_directory) / f"gen_{gen_num}"
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / "target_agent.py").write_text(f"# promoted gen {gen_num}\nprint('a')\n")
    (gen_dir / "results.json").write_text(json.dumps({"accuracy": accuracy}))
    (gen_dir / "target_agent_stdout.log").write_text("promoted stdout\n")
    return gen_dir


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
def test_promoted_generation_results_are_reused_not_rerun(mock_run_ta, mock_eval, mock_llm, tmp_path):
    """K>1: a promoted generation's results.json is preserved exactly (no re-run nondeterminism)."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    # Selected winner scored 89.6 and was promoted; a re-run would re-roll this number.
    gen1 = _seed_promoted_generation(run_setup, 1, 89.6)

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests

    run_generation(
        current_gen=1,
        max_gen=1,  # final generation -> no feedback step; isolates the run/eval skip
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # The promoted winner's accuracy is preserved byte-for-byte.
    assert json.loads((gen1 / "results.json").read_text())["accuracy"] == 89.6
    # No re-run, no re-eval of the promoted generation.
    assert mock_run_ta.call_count == 0
    assert mock_eval.call_count == 0


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_best_of_n_invokes_target_agent_exactly_k_times_per_gen(
    mock_run_agent, mock_run_ta, mock_eval, mock_llm, tmp_path
):
    """K>1: producing gen_{n+1} runs the candidate target K times; the promoted gen is not re-run (K, not K+1)."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    # gen_1 was promoted by best-of-N (already produced + evaluated).
    _seed_promoted_generation(run_setup, 1, 80.0)
    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 80.0}, "success": True}
    ]

    mock_run_agent.side_effect = _writing_run_agent(lambda d: Path(d, "target_agent.py").write_text("print('cand')\n"))
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _cand_dir_eval_factory({"cand_0": 82.0, "cand_1": 85.0, "cand_2": 81.0})

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests

    run_generation(
        current_gen=1,
        max_gen=2,  # produce gen_2 via best-of-N
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # Exactly K candidate runs for gen_2; the promoted gen_1 is NOT re-run (would be K+1).
    assert mock_run_ta.call_count == 3
    assert mock_eval.call_count == 3


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_base_on_best_sees_true_selected_accuracy(mock_run_agent, mock_run_ta, mock_eval, mock_llm, tmp_path):
    """K>1: base-on-best evolves from the generation whose preserved (selected) accuracy is highest.

    gen_1 was promoted at 90.0; gen_2 is being produced. With reuse, gen_1's 90.0 stands and the
    feedback candidates evolve from gen_1.
    """
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    gen1 = _seed_promoted_generation(run_setup, 1, 90.0)
    run_setup.context_mgr.generations = [
        {"gen_num": 1, "agent_stats": {"size": 100, "lines": 10}, "metrics": {"accuracy": 90.0}, "success": True}
    ]

    captured_base_gens = []

    mock_run_agent.side_effect = _writing_run_agent(lambda d: Path(d, "target_agent.py").write_text("print('cand')\n"))
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _cand_dir_eval_factory({"cand_0": 85.0, "cand_1": 88.0, "cand_2": 86.0})

    cfg = Config()
    cfg.BEST_OF_N = 3
    cfg.BEST_OF_N_STAGGER_SECONDS = 0  # no real sleep in tests
    cfg.BASE_ON_BEST = True

    with patch("sia.orchestrator._run_feedback_agent") as mock_fb:
        mock_fb.side_effect = lambda *a, **k: (
            captured_base_gens.append(k["base_gen"])
            or Path(k["next_gen_dir"], "target_agent.py").write_text("print('cand')\n")
        )
        run_generation(
            current_gen=1,
            max_gen=2,
            run_setup=run_setup,
            task_files=TaskFiles("d", "r", {}, "# T"),
            abs_dataset_dir=str(task_dir / "data" / "public"),
            dataset_dir=str(task_dir / "data" / "public"),
            meta_profile=DEFAULT_META_PROFILE,
            sandbox="none",
            env_config=cfg,
            task_model=DEFAULT_TASK_MODEL,
            target_provider=DEFAULT_TARGET_PROVIDER,
        )

    # gen_1's preserved accuracy (90.0) remains the basis; every candidate evolves from gen_1.
    assert json.loads((gen1 / "results.json").read_text())["accuracy"] == 90.0
    assert captured_base_gens == [1, 1, 1]


@patch("sia.context_manager.ContextManager._generate_llm_summary", return_value=None)
@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
def test_k1_path_still_runs_and_evaluates_current_generation(mock_run_ta, mock_eval, mock_llm, tmp_path):
    """K=1 (default): the current generation IS run + evaluated — unchanged from today."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)

    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([77.0])

    cfg = Config()  # BEST_OF_N defaults to 1

    run_generation(
        current_gen=1,
        max_gen=1,  # final generation -> isolates the run/eval of the current gen
        run_setup=run_setup,
        task_files=TaskFiles("d", "r", {}, "# T"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        meta_profile=DEFAULT_META_PROFILE,
        sandbox="none",
        env_config=cfg,
        task_model=DEFAULT_TASK_MODEL,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # K=1 path runs + evaluates the current generation exactly once (no skip).
    assert mock_run_ta.call_count == 1
    assert mock_eval.call_count == 1


def test_best_of_n_weights_focus_keeps_single_candidate_path(tmp_path):
    """focus='weights' with BEST_OF_N>1 must NOT enter best-of-N: weights mode is untouched."""
    task_dir, _ = _make_task_files(tmp_path)
    run_setup = _make_run_setup(tmp_path, task_dir)
    # train.py is the weights-mode agent file; seed it so the single path can run it.
    gen1 = Path(run_setup.run_directory) / "gen_1"
    (gen1 / "train.py").write_text("print('train')\n")

    cfg = Config()
    cfg.BEST_OF_N = 3  # would trigger best-of-N in harness mode

    with (
        patch("sia.orchestrator.run_evaluation") as mock_eval,
        patch("sia.orchestrator._run_target_agent") as mock_run_ta,
    ):
        mock_run_ta.return_value = (True, "out", "", "")
        mock_eval.side_effect = _candidate_eval_factory([55.0])

        run_generation(
            current_gen=1,
            max_gen=1,
            run_setup=run_setup,
            task_files=TaskFiles("d", "r", {}, "# T"),
            abs_dataset_dir=str(task_dir / "data" / "public"),
            dataset_dir=str(task_dir / "data" / "public"),
            meta_profile=DEFAULT_META_PROFILE,
            sandbox="none",
            env_config=cfg,
            task_model=DEFAULT_TASK_MODEL,
            target_provider=DEFAULT_TARGET_PROVIDER,
            focus="weights",
        )

    # The single-candidate weights path runs + evaluates the current generation once,
    # never the K-candidate / promoted-reuse path.
    assert mock_run_ta.call_count == 1
    assert mock_eval.call_count == 1
    assert not (gen1 / "cand_0").exists()


# --- _run_candidates_concurrently (the shared concurrency helper) -----------


def _candidate(name, accuracy, code_size=0):
    from sia.orchestrator import Candidate

    return Candidate(name=name, accuracy=accuracy, code_size=code_size)


def test_concurrent_candidates_gathered_in_index_order_with_winner():
    """K=3 all scored -> candidates returned in index order; select_best winner is stable."""
    from sia.orchestrator import _run_candidates_concurrently, select_best

    accuracies = [70.0, 85.0, 80.0]
    factories = [(lambda i=i: _candidate(f"cand_{i}", accuracies[i])) for i in range(3)]

    candidates = _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]
    assert [c.accuracy for c in candidates] == accuracies
    assert select_best(candidates).name == "cand_1"


def test_concurrent_result_order_independent_of_completion_order():
    """Candidates finishing out of order still gather by index -> deterministic winner."""
    import time as _time

    from sia.orchestrator import _run_candidates_concurrently, select_best

    # cand_0 sleeps longest so it finishes LAST, but must still land at index 0.
    sleeps = [0.06, 0.0, 0.03]
    accuracies = [88.0, 70.0, 90.0]

    def _factory(i):
        def _run():
            _time.sleep(sleeps[i])
            return _candidate(f"cand_{i}", accuracies[i])

        return _run

    factories = [_factory(i) for i in range(3)]
    candidates = _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]
    # Winner is cand_2 (90.0) regardless of completion order.
    assert select_best(candidates).name == "cand_2"


def test_concurrent_candidate_error_is_isolated_and_survivors_win():
    """A factory that raises becomes a None-accuracy candidate; survivors still compete."""
    from sia.orchestrator import _run_candidates_concurrently, select_best

    def _factory(i):
        def _run():
            if i == 1:
                raise RuntimeError("authoring blew up")
            return _candidate(f"cand_{i}", 70.0 if i == 0 else 80.0)

        return _run

    factories = [_factory(i) for i in range(3)]
    candidates = _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]
    assert candidates[1].accuracy is None  # cand_1 salvaged as unscored
    assert candidates[0].accuracy == 70.0
    assert candidates[2].accuracy == 80.0
    assert select_best(candidates).name == "cand_2"  # best survivor


def test_concurrent_all_fail_raises_runtime_error():
    """When every candidate is unscored (accuracy None), the helper raises RuntimeError."""
    from sia.orchestrator import _run_candidates_concurrently

    factories = [(lambda i=i: _candidate(f"cand_{i}", None)) for i in range(3)]

    with pytest.raises(RuntimeError, match="All 3 best-of-N candidates failed"):
        _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)


def test_concurrent_all_fail_raises_even_when_a_factory_raises():
    """All-fail guard fires even when the unscored state comes from raised factories."""
    from sia.orchestrator import _run_candidates_concurrently

    def _boom():
        raise RuntimeError("authoring blew up")

    factories = [_boom for _ in range(2)]

    with pytest.raises(RuntimeError, match="All 2 best-of-N candidates failed"):
        _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)


def test_concurrency_zero_runs_at_least_two_candidates_concurrently():
    """concurrency=0 (all-parallel) actually overlaps -> a Barrier of K rendezvous."""
    barrier = threading.Barrier(3, timeout=5)

    from sia.orchestrator import _run_candidates_concurrently

    def _factory(i):
        def _run():
            # If fewer than 3 threads run at once, this barrier times out (BrokenBarrierError).
            barrier.wait()
            return _candidate(f"cand_{i}", float(i))

        return _run

    factories = [_factory(i) for i in range(3)]
    candidates = _run_candidates_concurrently(factories, concurrency=0, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]


def test_concurrency_cap_limits_observed_parallelism():
    """concurrency=2 with K=3 caps observed concurrency at 2 (and never above)."""
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    from sia.orchestrator import _run_candidates_concurrently

    def _factory(i):
        def _run():
            import time as _time

            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            _time.sleep(0.05)
            with lock:
                state["active"] -= 1
            return _candidate(f"cand_{i}", float(i))

        return _run

    factories = [_factory(i) for i in range(3)]
    candidates = _run_candidates_concurrently(factories, concurrency=2, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]
    assert state["max_active"] == 2  # capped at the knob, K=3 not all-parallel


def test_concurrency_one_runs_sequentially_without_executor():
    """effective concurrency 1 -> plain sequential loop; candidates run one at a time."""
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    from sia.orchestrator import _run_candidates_concurrently

    def _factory(i):
        def _run():
            import time as _time

            with lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            _time.sleep(0.02)
            with lock:
                state["active"] -= 1
            return _candidate(f"cand_{i}", float(i))

        return _run

    factories = [_factory(i) for i in range(3)]
    candidates = _run_candidates_concurrently(factories, concurrency=1, stagger_seconds=0)

    assert [c.name for c in candidates] == ["cand_0", "cand_1", "cand_2"]
    assert state["max_active"] == 1  # strictly sequential


def test_best_of_n_concurrent_winner_matches_sequential_winner(tmp_path):
    """End-to-end: K=3 gen-1 best-of-N picks the same winner concurrently as sequentially."""
    from sia.orchestrator import _run_gen1_best_of_n

    def _run_once(concurrency):
        task_dir, _ = _make_task_files(tmp_path / f"c{concurrency}")
        run_setup = _make_run_setup(tmp_path / f"c{concurrency}", task_dir)

        def _write(working_dir):
            marker = Path(working_dir).name
            Path(working_dir, "target_agent.py").write_text(f"# {marker}\nprint('a')\n")

        with (
            patch("sia.orchestrator.run_agent") as mock_run_agent,
            patch("sia.orchestrator._run_target_agent") as mock_run_ta,
            patch("sia.orchestrator.run_evaluation") as mock_eval,
        ):
            mock_run_agent.side_effect = _writing_run_agent(_write)
            mock_run_ta.return_value = (True, "out", "", "")
            mock_eval.side_effect = _cand_dir_eval_factory({"cand_0": 70.0, "cand_1": 85.0, "cand_2": 80.0})

            cfg = Config()
            cfg.BEST_OF_N = 3
            cfg.BEST_OF_N_CONCURRENCY = concurrency
            cfg.BEST_OF_N_STAGGER_SECONDS = 0

            _run_gen1_best_of_n(
                run_setup=run_setup,
                task_files=_make_gen1_task_files(),
                task_model=DEFAULT_TASK_MODEL,
                meta_profile=DEFAULT_META_PROFILE,
                abs_dataset_dir=str(task_dir / "data" / "public"),
                dataset_dir=str(task_dir / "data" / "public"),
                sandbox="none",
                env_config=cfg,
                target_provider=DEFAULT_TARGET_PROVIDER,
            )

        gen1 = Path(run_setup.run_directory) / "gen_1"
        return json.loads((gen1 / "results.json").read_text())["accuracy"]

    sequential_winner = _run_once(concurrency=1)
    concurrent_winner = _run_once(concurrency=0)
    assert sequential_winner == concurrent_winner == 85.0


# --- candidate runner threads instance max_turns + salvages on error ---------


@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_meta_candidate_threads_instance_max_turns_to_run_agent(mock_run_agent, mock_run_ta, mock_eval, tmp_path):
    """A candidate run with SIA_MAX_TURNS=7 passes max_turns='7' to run_agent (instance, not class default)."""
    from sia.orchestrator import _run_meta_candidate

    captured = {}

    async def _capture(
        *, model_name, max_turns, prompt, agent_working_directory, agent_impl, provider=None, protected_paths=None
    ):
        captured["max_turns"] = max_turns
        Path(agent_working_directory, "target_agent.py").write_text("print('a')\n")

    mock_run_agent.side_effect = _capture
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([77.0])

    task_dir, _ = _make_task_files(tmp_path)
    cand_dir = str(tmp_path / "gen_1" / "cand_0")

    env_config = Config()
    env_config.DEFAULT_MAX_TURNS = 7  # what Config.from_env() yields for SIA_MAX_TURNS=7

    _run_meta_candidate(
        cand_dir=cand_dir,
        task_files=_make_gen1_task_files(),
        task_model=DEFAULT_TASK_MODEL,
        meta_profile=DEFAULT_META_PROFILE,
        venv_dir=str(tmp_path / "venv"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        sandbox="none",
        env_config=env_config,
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    # The value threaded to run_agent is the instance's override, not the class default.
    assert captured["max_turns"] == "7"


@patch("sia.orchestrator.run_evaluation")
@patch("sia.orchestrator._run_target_agent")
@patch("sia.orchestrator.run_agent")
def test_meta_candidate_salvages_scaffold_on_authoring_error(mock_run_agent, mock_run_ta, mock_eval, tmp_path):
    """A meta-agent that raises (e.g. max_turns) still yields a Candidate from its scaffold."""
    from sia.orchestrator import _run_meta_candidate

    async def _write_then_raise(
        *, model_name, max_turns, prompt, agent_working_directory, agent_impl, provider=None, protected_paths=None
    ):
        # Simulate an agent that wrote a working scaffold before hitting max_turns.
        Path(agent_working_directory, "target_agent.py").write_text("print('salvaged')\n")
        raise RuntimeError("max_turns hit")

    mock_run_agent.side_effect = _write_then_raise
    mock_run_ta.return_value = (True, "out", "", "")
    mock_eval.side_effect = _candidate_eval_factory([66.0])

    task_dir, _ = _make_task_files(tmp_path)
    cand_dir = str(tmp_path / "gen_1" / "cand_0")

    # Does not raise; the salvaged scaffold is run + scored.
    candidate = _run_meta_candidate(
        cand_dir=cand_dir,
        task_files=_make_gen1_task_files(),
        task_model=DEFAULT_TASK_MODEL,
        meta_profile=DEFAULT_META_PROFILE,
        venv_dir=str(tmp_path / "venv"),
        abs_dataset_dir=str(task_dir / "data" / "public"),
        dataset_dir=str(task_dir / "data" / "public"),
        sandbox="none",
        env_config=Config(),
        target_provider=DEFAULT_TARGET_PROVIDER,
    )

    assert candidate.accuracy == 66.0
    assert Path(cand_dir, "target_agent.py").read_text() == "print('salvaged')\n"
