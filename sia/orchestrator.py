"""
Directory structure (conceptual)

orchestration/
  orchestrator.py

tasks/
  task_1/
    reference/
      reference_target_agent.py
      SAMPLE_TASK_DESCRIPTIONS.md
    data/
      public/
        train.csv
        test.csv
        task.md
      private/
  task_2/
    reference/
      reference_target_agent.py
      SAMPLE_TASK_DESCRIPTIONS.md
    data/
      public/
        task.md
      private/

tasks/_shared/                 # cross-task examples/templates (public)
  sample_agent_execution.json

runs/
  run_1/ (unique meta_agent, unique feedback_agent, unique_task, reference_target_agent, config)
    gen_1: (meta_agent, reference_target_agent) -> target_agent_1 -> gen_1
    gen_2: (feedback_agent, target_agent_1) -> target_agent_2 -> gen_2
    gen_3: (feedback_agent, target_agent_2) -> target_agent_3 -> gen_3
  run_2/ (unique meta_agent, unique feedback_agent, unique_task, reference_target_agent, config)
    gen_1: (meta_agent, reference_target_agent) -> target_agent_1 -> gen_1
    gen_2: (feedback_agent, target_agent_1) -> target_agent_2 -> gen_2
    gen_3: (feedback_agent, target_agent_2) -> target_agent_3 -> gen_3
  run_3/ (unique meta_agent, unique feedback_agent, unique_task, reference_target_agent, config)
    gen_1: (meta_agent, reference_target_agent) -> target_agent_1 -> gen_1
    gen_2: (feedback_agent, target_agent_1) -> target_agent_2 -> gen_2
    gen_3: (feedback_agent, target_agent_2) -> target_agent_3 -> gen_3
"""

import asyncio
import glob
import json
import os
import random
import shutil
import subprocess
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sia import __version__, cli
from sia.agent_reference import ResolvedAgentReference, copy_reference_into, resolve_agent_reference
from sia.config import Config
from sia.context_manager import ContextManager, parse_accuracy, summarize_items
from sia.io_utils import file_size_ok, write_text
from sia.layout import BUNDLED_TASKS, Names, RunLayout, TaskLayout, resolve_task_dir, venv_python_path
from sia.logging_setup import configure_logging, get_logger
from sia.profiles import MetaAgentProfile, load_meta_agent_profile, load_target_agent_profile
from sia.prompts import HELD_OUT_GROUND_TRUTH_NOTICE, build_feedback_prompt, build_meta_prompt
from sia.providers import Provider
from sia.results import FeedbackContext, TargetAgentResult
from sia.run_setup import RunSetup, TaskFiles, install_requirements, load_task_files, setup_run_directory
from sia.util import run_agent

__all__ = [
    "BUNDLED_TASKS",
    "HELD_OUT_GROUND_TRUTH_NOTICE",
    "Candidate",
    "RunSetup",
    "TaskFiles",
    "build_feedback_prompt",
    "build_meta_prompt",
    "compute_protected_paths",
    "load_agent_execution",
    "load_task_files",
    "main",
    "resolve_task_dir",
    "run_evaluation",
    "run_generation",
    "select_best",
    "setup_run_directory",
]

logger = get_logger(__name__)


# ========================
# HELPER FUNCTIONS
# ========================


def compute_protected_paths(task_dir: str) -> list[str]:
    """Return the task's held-out ground-truth dirs that meta/feedback agents must not read.

    Generic across all SIA tasks: the public/private convention places grader-only ground
    truth under ``<task_dir>/data/private``. Returns the absolute path when that dir exists,
    or an empty list (no-op) when the task ships no private dir.
    """
    private_dir = os.path.join(task_dir, "data/private")
    if os.path.isdir(private_dir):
        return [os.path.abspath(private_dir)]
    return []


def load_agent_execution(gen_directory, config: Config | None = None):
    """
    Load execution logs with automatic format detection.

    Supports two formats:
    1. Single-file: gen_X/agent_execution.json (backwards compatible)
    2. Multi-trajectory: gen_X/agent_execution/execution_q0.json, execution_q1.json, ...

    Args:
        gen_directory: Path to the generation directory
        config: Optional Config instance (defaults to Config()).

    Returns:
        tuple: (execution_data, is_multi_trajectory)
            - execution_data: dict or list containing execution log(s)
            - is_multi_trajectory: bool indicating if multi-trajectory format
    """
    cfg = config or Config()
    execution_folder = os.path.join(gen_directory, Names.AGENT_EXECUTION_DIR)
    execution_file = os.path.join(gen_directory, Names.AGENT_EXECUTION_JSON)

    # Multi-trajectory folder: one file per question
    if os.path.isdir(execution_folder):
        logger.info("  → Detected multi-trajectory format (folder)")

        files = sorted(glob.glob(os.path.join(execution_folder, Names.EXECUTION_GLOB)))

        if not files:
            logger.warning("  ✗ agent_execution/ folder exists but is empty")
            return {"error": "Empty execution folder", "type": "multi-trajectory"}, True

        # Load all trajectory files
        trajectories = []
        for f in files:
            try:
                within_limit, file_size = file_size_ok(f, cfg.MAX_EXECUTION_LOG_SIZE)
                if not within_limit:
                    logger.warning(f"Skipping oversized trajectory ({file_size:,} bytes): {os.path.basename(f)}")
                    trajectories.append({"error": "File too large", "file": os.path.basename(f), "size": file_size})
                    continue
                with open(f, encoding="utf-8") as fp:
                    trajectories.append(json.load(fp))
            except json.JSONDecodeError as e:
                logger.warning(f"  ✗ Failed to parse {os.path.basename(f)}: {e}")
                trajectories.append({"error": str(e), "file": os.path.basename(f)})
            except (OSError, KeyError) as e:
                logger.warning(f"  ✗ Error reading {os.path.basename(f)}: {e}")
                trajectories.append({"error": str(e), "file": os.path.basename(f)})

        logger.info(f"  ✓ Loaded {len(trajectories)} trajectory files")

        return {"trajectories": trajectories, "count": len(trajectories), "type": "multi-trajectory"}, True

    # Single combined execution file
    elif os.path.exists(execution_file):
        logger.info("  → Detected single-file format")

        try:
            within_limit, file_size = file_size_ok(execution_file, cfg.MAX_EXECUTION_LOG_SIZE)
            if not within_limit:
                logger.warning(f"Execution log too large ({file_size:,} bytes), skipping")
                return {"error": "File too large", "size": file_size}, False
            with open(execution_file, encoding="utf-8") as f:
                data = json.load(f)
            logger.info("  ✓ Successfully loaded agent execution log")
            return data, False

        except json.JSONDecodeError as e:
            logger.warning(f"  ✗ Failed to parse agent_execution.json: {e}")
            logger.warning("  → The target agent may have crashed or failed to complete")

            # Return partial data for debugging
            try:
                with open(execution_file, encoding="utf-8") as f:
                    raw = f.read()
                return {
                    "error": "Parse error",
                    "raw_preview": raw[:1000],
                    "parse_error": str(e),
                    "file_size": len(raw),
                }, False
            except OSError as read_error:
                return {"error": "Could not read file", "read_error": str(read_error)}, False

        except FileNotFoundError:
            logger.error("  ✗ agent_execution.json not found")
            return {"error": "Execution log file not found"}, False

    # Neither exists
    else:
        logger.error("  ✗ No execution log found (neither file nor folder)")
        return {"error": "Execution log not found"}, False


def run_evaluation(gen_directory, task_dir, venv_dir, config: Config | None = None):
    """
    Run evaluate.py if it exists in the task's public data directory.

    Args:
        gen_directory: Path to the generation directory containing submission files
        task_dir: Path to the task directory
        venv_dir: Path to the virtual environment
        config: Optional Config instance (defaults to Config()).

    Returns:
        dict: Evaluation results or error information
    """
    cfg = config or Config()

    # Look for evaluate.py in data/public/ first, then fall back to task_dir
    evaluate_script = TaskLayout(task_dir, "").evaluate_script()

    if evaluate_script is None:
        logger.info(f"  → No evaluate.py found in {task_dir}, skipping evaluation")
        return {"status": "skipped", "reason": "evaluate.py not found"}

    logger.info(f"Running evaluation script: {evaluate_script}")

    # Create evaluation log file
    eval_log_file = os.path.join(gen_directory, Names.EVAL_LOG)
    logger.info(f"  → Evaluation log: {eval_log_file}")

    # Run evaluate.py as subprocess with --gen-dir
    try:
        python_exec = venv_python_path(venv_dir)
        result = subprocess.run(
            [python_exec, evaluate_script, "--gen-dir", gen_directory],
            capture_output=True,
            text=True,
            timeout=cfg.EVAL_TIMEOUT,
        )
        # Write combined output to log file
        eval_output = result.stdout + result.stderr
        write_text(eval_log_file, eval_output)

        if result.returncode != 0:
            logger.error(f"  ✗ Evaluation failed with exit code {result.returncode}")
            return {
                "status": "error",
                "reason": f"evaluate.py exited with code {result.returncode}",
                "log_path": eval_log_file,
                "output": eval_output,
            }

        # Check if results.json was created
        results_json_path = os.path.join(gen_directory, Names.RESULTS_JSON)
        if os.path.exists(results_json_path):
            logger.info("  ✓ Evaluation completed successfully")
            logger.info(f"  ✓ Results saved to: {results_json_path}")

            # Load and log results
            try:
                with open(results_json_path) as f:
                    results = json.load(f)
                logger.info(f"    Results: {json.dumps(results, indent=2)}")
            except (json.JSONDecodeError, OSError):
                pass

            return {
                "status": "success",
                "log_path": eval_log_file,
                "results_path": results_json_path,
                "output": eval_output,
            }
        else:
            logger.warning("  ⚠ Evaluation completed but results.json not found")
            return {
                "status": "warning",
                "reason": "results.json not created by evaluate.py",
                "log_path": eval_log_file,
                "output": eval_output,
            }

    except subprocess.TimeoutExpired:
        logger.error(f"  ✗ Evaluation timed out after {cfg.EVAL_TIMEOUT}s")
        return {"status": "error", "reason": f"Evaluation timed out after {cfg.EVAL_TIMEOUT}s"}
    except (subprocess.SubprocessError, OSError) as e:
        logger.error(f"  ✗ Unexpected error during evaluation: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "reason": str(e), "traceback": traceback.format_exc()}


def _print_welcome():
    banner = rf"""
     _______. __       ___
    /       ||  |     /   \
   |   (----`|  |    /  ^  \
    \   \    |  |   /  /_\  \
.----)   |   |  |  /  _____  \
|_______/    |__| /__/     \__\

    Self-Improving AI framework

    • Version : v{__version__}
    • Docs    : https://github.com/hexo-ai/sia
    • Help    : sia --help
"""
    print(banner)


def _stream_to_log(cmd: list[str], stdout_log_file: str, env: dict | None = None) -> int:
    """Run ``cmd``, streaming merged stdout/stderr to the console and a log file.

    Returns the process exit code. This is the single place the target agent
    subprocess is launched; the Popen call stays in this module's namespace so it
    remains patchable in tests.
    """
    with open(stdout_log_file, "w", encoding="utf-8") as log_fh:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        for line in process.stdout:
            print(line, end="")
            log_fh.write(line)
        return process.wait()


def _run_target_agent_sandboxed(
    python_exec: str,
    target_agent_path: str,
    dataset_dir: str,
    working_dir: str,
    stdout_log_file: str,
    config: Config,
    sandbox_url: str = "http://localhost:8080",
) -> int:
    """Run target agent inside a Docker container for isolation.

    Mounts dataset_dir as read-only and working_dir as read-write.
    Network access is disabled.
    Passes SANDBOX_URL environment variable for SandboxFusion connectivity.
    """
    docker_cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--memory",
        config.DOCKER_MEMORY_LIMIT,
        f"--cpus={config.DOCKER_CPU_LIMIT}",
        "-e",
        f"SANDBOX_URL={sandbox_url}",
        "-v",
        f"{dataset_dir}:/data:ro",
        "-v",
        f"{working_dir}:/work:rw",
        config.DOCKER_IMAGE,
        "python",
        "-u",
        "/work/target_agent.py",
        "--dataset_dir",
        "/data",
        "--working_dir",
        "/work",
    ]

    return _stream_to_log(docker_cmd, stdout_log_file)


def _run_target_agent(
    venv_dir: str,
    target_agent_path: str,
    abs_dataset_dir: str,
    gen_dir: str,
    stdout_log_file: str,
    sandbox: str,
    env_config: Config,
) -> tuple[bool, str, str, str]:
    """Run the target agent subprocess.

    Returns (success, stdout, stderr, error_msg).
    """
    python_exec = venv_python_path(venv_dir)

    # Prepare environment with SANDBOX_URL for SandboxFusion connectivity
    # This allows target agents (especially train.py with RL) to reach the SandboxFusion service
    env = os.environ.copy()
    sandbox_url = os.getenv("SANDBOX_URL", "http://localhost:8080")
    env["SANDBOX_URL"] = sandbox_url
    logger.info(f"  → SANDBOX_URL: {sandbox_url}")

    try:
        if sandbox == "docker":
            # Use Docker-internal address for SandboxFusion connectivity from container
            docker_sandbox_url = os.getenv("SANDBOX_URL", "http://host.docker.internal:8080")
            return_code = _run_target_agent_sandboxed(
                python_exec=python_exec,
                target_agent_path=target_agent_path,
                dataset_dir=abs_dataset_dir,
                working_dir=gen_dir,
                stdout_log_file=stdout_log_file,
                config=env_config,
                sandbox_url=docker_sandbox_url,
            )
        else:
            cmd = [python_exec, "-u", target_agent_path, "--dataset_dir", abs_dataset_dir, "--working_dir", gen_dir]
            return_code = _stream_to_log(cmd, stdout_log_file, env=env)

        with open(stdout_log_file, encoding="utf-8") as f:
            stdout = f.read()

        logger.info("=" * 60)

        if return_code != 0:
            error_msg = f"Target agent failed with exit code {return_code}"
            logger.error(f"  ✗ Target agent execution failed with exit code {return_code}")
            logger.warning("  → Continuing with feedback agent despite target agent failure")
            return TargetAgentResult(False, stdout, "", error_msg).as_tuple()
        else:
            logger.info("  ✓ Target agent execution completed successfully")
            return TargetAgentResult(True, stdout, "", "").as_tuple()

    except FileNotFoundError:
        logger.error(f"  ✗ Target agent file not found: {target_agent_path}")
        logger.error("  → Cannot continue.")
        return TargetAgentResult(False, "", "", f"Target agent file not found: {target_agent_path}").as_tuple()
    except Exception as e:
        error_msg = f"Unexpected error during target agent execution: {e!s}"
        logger.exception(f"  ✗ {error_msg}")
        logger.warning("  → Continuing with feedback agent despite target agent failure")
        stdout = ""
        try:
            with open(stdout_log_file, encoding="utf-8") as f:
                stdout = f.read()
        except OSError:
            pass
        return TargetAgentResult(False, stdout, "", error_msg).as_tuple()


# Generic render whitelist for a single failing eval item. Only these keys reach the
# feedback prompt — no task-shaped key (e.g. a reference answer carried elsewhere) can
# ride along.
_ITEM_RENDER_FIELDS = ("id", "group", "status", "category", "input", "output", "detail")

_ANTI_REWARD_HACK_FRAMING = (
    "The held-out reference answers are intentionally withheld. Improve the agent by "
    "reasoning about WHY these inputs failed (the failing inputs/outputs above) — "
    "never by hardcoding or matching specific answers."
)


def _select_failures(items: list[Any], pass_statuses: tuple[str, ...], max_failures: int) -> list[dict]:
    """Pick up to `max_failures` FAILED items, diversified across status and group.

    Round-robins over (status, group) buckets so a single dominant status or group
    cannot crowd out the others — the feedback agent sees a spread of failure modes.
    Reads only the generic `status` and `group` item keys.
    """
    pass_set = set(pass_statuses)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status in pass_set:
            continue
        key = (str(status), str(item.get("group")))
        buckets.setdefault(key, []).append(item)

    selected: list[dict] = []
    bucket_lists = list(buckets.values())
    cursor = 0
    while len(selected) < max_failures and any(bucket_lists):
        bucket = bucket_lists[cursor % len(bucket_lists)]
        if bucket:
            selected.append(bucket.pop(0))
        cursor += 1
        if cursor % len(bucket_lists) == 0:
            bucket_lists = [b for b in bucket_lists if b]
            cursor = 0
    return selected


def _render_item(item: dict) -> dict:
    """Project a failing item onto the generic render whitelist (present keys only).

    Constructs a NEW dict containing only `_ITEM_RENDER_FIELDS` keys that are present
    in the item — never copies the source dict, so no task-specific key (and no
    reference answer carried elsewhere) can ride along.
    """
    return {field: item[field] for field in _ITEM_RENDER_FIELDS if field in item}


def _build_eval_summary(
    eval_data: dict,
    env_config: Config,
) -> str:
    """Render a curated, reference-answer-free eval summary for the feedback prompt.

    Task-agnostic: reads top-level scalar counts plus the generic `items[]` array ONLY —
    never `eval_data["results"]` (the task-shaped record that may carry a reference
    answer). Emits a scalar header, a capped sample of FAILED items diversified across
    `status` and `group` (each rendered with only the generic `_ITEM_RENDER_FIELDS`
    present), and an anti-reward-hack framing line.
    """
    scalars = (
        f"- accuracy_percent: {eval_data.get('accuracy_percent')}",
        f"- correct: {eval_data.get('correct')}",
        f"- wrong_answer: {eval_data.get('wrong_answer')}",
        f"- exec_error: {eval_data.get('exec_error')}",
        f"- missing: {eval_data.get('missing')}",
    )
    header = "**Held-out eval (scalars)**:\n" + "\n".join(scalars)

    items = eval_data.get("items")
    if not isinstance(items, list):
        items = []

    # Lever B — aggregate failure-taxonomy breakdown (reference-answer-free: status/group/
    # category counts only). Gives the feedback agent the "where failures concentrate" signal
    # the per-item sample alone (capped) does not. Empty string when disabled or no failures.
    taxonomy_block = ""
    if env_config.FAILURE_TAXONOMY and items:
        summary = summarize_items(items, env_config.VERIFIER_PASS_STATUSES)
        if summary.get("failures"):
            top_n = env_config.FAILURE_TAXONOMY_TOP_N
            status_str = ", ".join(f"{s} ({n})" for s, n in summary.get("status_counts", {}).items())
            top_groups = sorted(summary.get("group_failure_counts", {}).items(), key=lambda kv: -kv[1])[:top_n]
            groups_str = ", ".join(f"`{g}` ({n})" for g, n in top_groups)
            lines = [
                f"\n\n**Failure breakdown** ({summary['failures']} of {summary.get('total', len(items))} items failed):"
            ]
            if status_str:
                lines.append(f"- by status: {status_str}")
            if groups_str:
                lines.append(f"- concentrated in groups (top {top_n}): {groups_str}")
            taxonomy_block = "\n".join(lines)

    failures = _select_failures(items, env_config.VERIFIER_PASS_STATUSES, env_config.FEEDBACK_FAILURE_SAMPLES)
    if failures:
        shown = [_render_item(item) for item in failures]
        failures_block = (
            f"\n\n**Sample of FAILED held-out items** "
            f"(up to {env_config.FEEDBACK_FAILURE_SAMPLES}, diversified across status and group):\n"
            f"```json\n{json.dumps(shown, indent=2)}\n```"
        )
    else:
        failures_block = "\n\nNo failed held-out items to show (all passed or no per-item detail available)."

    return f"{header}{taxonomy_block}{failures_block}\n\n{_ANTI_REWARD_HACK_FRAMING}"


def _build_feedback_context(
    current_gen: int,
    gen_dir: str,
    dataset_dir: str,
    target_agent_success: bool,
    target_agent_error_msg: str,
    target_agent_stdout: str,
    target_agent_stderr: str,
    stdout_log_file: str,
    task_files: TaskFiles,
    config: Config | None = None,
) -> tuple[str, str]:
    """Build execution status and section for feedback prompt.

    Returns (execution_status, execution_section).
    """
    cfg = config or Config()

    # Load execution log
    agent_execution, is_multi_trajectory = load_agent_execution(gen_dir, config=cfg)

    if is_multi_trajectory:
        trajectory_count = agent_execution.get("count", 0)
        trajectories = agent_execution.get("trajectories", [])

        successful = sum(1 for t in trajectories if isinstance(t, list))
        failed = sum(1 for t in trajectories if isinstance(t, dict) and t.get("error"))

        sample_trajectories_text = ""
        for idx, traj in enumerate(trajectories[:3]):
            traj_json = json.dumps(traj, indent=2)
            if len(traj_json) > cfg.TRAJECTORY_PREVIEW_LIMIT:
                traj_json = traj_json[: cfg.TRAJECTORY_PREVIEW_LIMIT] + "\n  ... (truncated)"
            sample_trajectories_text += f"\n### Trajectory {idx}\n```json\n{traj_json}\n```\n"

        execution_section = f"""
**MULTI-TRAJECTORY EXECUTION**:

The agent executed {trajectory_count} separate trajectories (e.g., different questions/samples).

**Summary**:
- Total trajectories: {trajectory_count}
- Successful: {successful}
- Failed: {failed}
- Execution folder: {os.path.join(gen_dir, Names.AGENT_EXECUTION_DIR)}

**Sample Trajectories** (first 3 shown, you can read others from the folder):
{sample_trajectories_text}

**To analyze all trajectories**:
- Read files from: {os.path.join(gen_dir, Names.AGENT_EXECUTION_DIR)}
- Files named: execution_q0.json, execution_q1.json, ..., execution_q{trajectory_count - 1}.json

**Analysis guidance**:
- Look for common failure patterns across trajectories
- Check if trajectories are properly isolated
- Ensure consistent behavior across all samples
"""
    else:
        traj_json = json.dumps(agent_execution, indent=2)
        if len(traj_json) > cfg.TRAJECTORY_PREVIEW_LIMIT:
            traj_json = traj_json[: cfg.TRAJECTORY_PREVIEW_LIMIT] + "\n  ... (truncated)"
        execution_section = f"""
Here is the target agent execution trajectory:
```json
{traj_json}
```

NOTE: If you see an "error" field in the above JSON, it means the execution log was malformed or missing. Focus on making the agent more robust.
"""

    # Load evaluation results if available
    eval_results_section = ""
    results_json_path = os.path.join(gen_dir, Names.RESULTS_JSON)
    if os.path.exists(results_json_path):
        try:
            within_limit, file_size = file_size_ok(results_json_path, cfg.MAX_EXECUTION_LOG_SIZE)
            if not within_limit:
                eval_results_section = f"\n**EVALUATION RESULTS**: results.json too large ({file_size:,} bytes)\n"
            else:
                with open(results_json_path, encoding="utf-8") as f:
                    eval_data = json.load(f)
                # Curated, gold-free summary — the full results dump leaked every
                # held-out reference answer to the feedback agent (reward-hacking
                # surface + turn pressure). Reads only the generic `items[]` contract.
                eval_summary = _build_eval_summary(eval_data, cfg)
                eval_results_section = f"""

**EVALUATION RESULTS**:
{eval_summary}
"""
        except (json.JSONDecodeError, OSError) as e:
            eval_results_section = f"\n**EVALUATION RESULTS**: Error loading results.json: {e}\n"
    else:
        eval_results_section = (
            "\n**EVALUATION RESULTS**: No results.json found (evaluation may not have run or may have failed)\n"
        )

    # Build execution status
    stdout_lines = target_agent_stdout.split("\n")
    last_10_lines = "\n".join(stdout_lines[-10:]) if len(stdout_lines) > 10 else target_agent_stdout

    if target_agent_success:
        execution_status = f"""SUCCESS: Target agent completed execution successfully.
{eval_results_section}

**Last 10 lines of output**:
```
{last_10_lines}
```

Full logs available at: {stdout_log_file}
"""
    else:
        execution_status = f"""FAILED: {target_agent_error_msg}
{eval_results_section}

**Last 10 lines of output**:
```
{last_10_lines}
```

Full logs available at: {stdout_log_file}

STDERR:
{target_agent_stderr}
"""

    return FeedbackContext(execution_status, execution_section).as_tuple()


def _run_feedback_agent(
    current_gen: int,
    max_gen: int,
    run_dir: str,
    next_gen_dir: str,
    task_files: TaskFiles,
    execution_status: str,
    execution_section: str,
    meta_profile: MetaAgentProfile,
    env_config: Config,
    dataset_dir: str,
    task_model: str,
    target_provider: Provider,
    focus: str = "harness",
    resolved_ref: ResolvedAgentReference | None = None,
    base_gen: int | None = None,
) -> None:
    """Run the feedback agent to create an improved target agent or train.py.

    Args:
        focus: "harness" (default) for code improvement or "weights" for RL-based tuning
        base_gen: which generation's ``target_agent.py`` to evolve from. When None
            (default), evolves from ``current_gen`` (byte-identical to today). Base-on-best
            routing passes the best-so-far generation instead. Harness-mode only; weights
            mode ignores it (its agent file is read by focus below).
    """
    # Read the appropriate agent file based on focus mode
    source_gen = current_gen if (base_gen is None or focus == "weights") else base_gen
    gen_dir = os.path.join(run_dir, f"gen_{source_gen}")
    if focus == "weights":
        agent_file = os.path.join(gen_dir, Names.TRAIN_SCRIPT)
    else:
        agent_file = os.path.join(gen_dir, Names.TARGET_AGENT)

    agent_py = Path(agent_file).read_text(encoding="utf-8")
    task = Path(dataset_dir, "task.md").read_text(encoding="utf-8")

    previous_gens_list = list(range(1, current_gen)) if current_gen > 1 else []
    previous_gens_text = ", ".join(map(str, previous_gens_list)) if previous_gens_list else "None"

    # Tell the feedback agent it may evolve dependencies whenever the reference uses a
    # requirements.txt (a directory reference, or a default/file reference shipping one).
    requirements_dir = next_gen_dir if (resolved_ref and resolved_ref.requirements) else None

    feedback_agent_prompt = build_feedback_prompt(
        current_gen=current_gen,
        max_gen=max_gen,
        task_files=task_files,
        agent_py=agent_py,
        task=task,
        execution_status=execution_status,
        execution_section=execution_section,
        run_dir=run_dir,
        next_gen_dir=next_gen_dir,
        previous_gens=previous_gens_text,
        task_model=task_model,
        provider=target_provider,
        requirements_dir=requirements_dir,
        focus=focus,
    )

    os.makedirs(next_gen_dir, exist_ok=True)

    # Carry the reference's helper files + requirements.txt into the next generation so
    # the improved target_agent.py can import them and declared deps get installed.
    if resolved_ref is not None:
        copy_reference_into(resolved_ref, next_gen_dir)

    feedback_prompt_path = os.path.join(next_gen_dir, Names.FEEDBACK_PROMPT)
    write_text(feedback_prompt_path, feedback_agent_prompt)
    logger.info(f"  ✓ Saved feedback agent prompt to: {feedback_prompt_path}")

    # dataset_dir is <task_dir>/data/public; the held-out dir is its sibling data/private.
    task_dir = os.path.dirname(os.path.dirname(dataset_dir))

    asyncio.run(
        run_agent(
            model_name=meta_profile.model,
            max_turns=str(env_config.DEFAULT_MAX_TURNS),
            prompt=feedback_agent_prompt,
            agent_working_directory=next_gen_dir,
            agent_impl=meta_profile.agent_impl,
            provider=meta_profile.provider,
            protected_paths=compute_protected_paths(task_dir),
        )
    )

    next_gen = current_gen + 1
    logger.info(f"Feedback agent completed. Created improved agent for generation {next_gen}")


# ========================
# BEST-OF-N (harness-only): parallel candidate execution + selection
# ========================


@dataclass
class Candidate:
    """One best-of-N candidate scaffold: its directory name and evaluated accuracy.

    `accuracy` is None when the candidate produced no parseable score; `code_size` is
    the byte length of its `target_agent.py` (used only as a tie-break).
    """

    name: str
    accuracy: float | None
    code_size: int


def select_best(
    candidates: list[Candidate],
    *,
    selection: str = "accuracy",
    tiebreak: str = "smaller_code",
    minibatch_frac: float | None = None,
) -> Candidate:
    """Pick the winning candidate by verifier accuracy, smaller-code on a tie.

    v1 evaluates every candidate on the full set and selects argmax accuracy directly.
    Candidates with no parseable accuracy rank below any scored candidate; the
    smaller-code tie-break breaks accuracy ties, and original list order is the final
    deterministic tie-break. With a single candidate (or all-None accuracies) the
    defined fallback is the first candidate — never raises.

    `selection`/`tiebreak` name the active policy (today only the defaults are wired);
    `minibatch_frac` is reserved for a later minibatch-rank follow-up and is ignored in
    v1 — its presence keeps the signature forward-compatible without rework.
    """
    if not candidates:
        raise ValueError("select_best requires at least one candidate")

    if all(candidate.accuracy is None for candidate in candidates):
        # No verifier signal anywhere -> the defined fallback is first-by-order, with no
        # smaller-code reordering (code size only matters to break a real accuracy tie).
        return candidates[0]

    def sort_key(indexed: tuple[int, Candidate]) -> tuple[int, float, int, int]:
        index, candidate = indexed
        scored = candidate.accuracy is not None
        accuracy = candidate.accuracy if scored else 0.0
        # Sort descending on (has-score, accuracy), ascending on (code_size, order):
        # negate the maximize dimensions so a plain min() yields the winner.
        return (0 if scored else 1, -accuracy, candidate.code_size, index)

    return min(enumerate(candidates), key=sort_key)[1]


def _read_candidate_accuracy(cand_dir: str) -> float | None:
    """Read a candidate's evaluated accuracy from its results.json (None if unscored)."""
    results_path = os.path.join(cand_dir, Names.RESULTS_JSON)
    if not os.path.exists(results_path):
        return None
    try:
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(results, dict):
        return None
    return parse_accuracy(results.get("accuracy"))


def _make_candidate_record(cand_dir: str) -> Candidate:
    """Build a Candidate from a produced+evaluated candidate directory."""
    agent_path = os.path.join(cand_dir, Names.TARGET_AGENT)
    code_size = os.path.getsize(agent_path) if os.path.exists(agent_path) else 0
    return Candidate(
        name=os.path.basename(cand_dir),
        accuracy=_read_candidate_accuracy(cand_dir),
        code_size=code_size,
    )


# Artifacts promoted from a winning candidate dir up to its parent generation dir, so
# all downstream code reads gen_N/ exactly as in the single-candidate path. The set must
# mirror everything a normal target-run + evaluation would otherwise produce in gen_N/,
# because the promoted generation is reused directly (no re-run) when BEST_OF_N > 1.
# `responses.json` has no layout.Names constant (it is a task-specific run artifact) so it
# stays a literal here; everything else routes through Names to survive upstream renames.
_PROMOTED_FILES = (
    Names.TARGET_AGENT,
    Names.RESULTS_JSON,
    "responses.json",
    Names.IMPROVEMENT_MD,
    Names.STDOUT_LOG,
)
_PROMOTED_DIRS = (Names.AGENT_EXECUTION_DIR,)


def _promote_candidate(winner_dir: str, dest_dir: str) -> None:
    """Copy a winning candidate's artifacts up into its parent generation directory.

    Files and the multi-trajectory execution dir are copied (overwriting) so downstream
    code (add_generation, _build_feedback_context, best_generation) reads dest_dir as
    today — the candidate machinery stays invisible above the promotion boundary.
    """
    for filename in _PROMOTED_FILES:
        src = os.path.join(winner_dir, filename)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest_dir, filename))
    for dirname in _PROMOTED_DIRS:
        src = os.path.join(winner_dir, dirname)
        if os.path.isdir(src):
            dest = os.path.join(dest_dir, dirname)
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            shutil.copytree(src, dest)


def _run_meta_candidate(
    cand_dir: str,
    task_files: TaskFiles,
    task_model: str,
    meta_profile: MetaAgentProfile,
    venv_dir: str,
    abs_dataset_dir: str,
    dataset_dir: str,
    sandbox: str,
    env_config: Config,
    target_provider: Provider,
    protected_paths: list[str] | None = None,
) -> Candidate:
    """Produce + run + evaluate one gen-1 meta-agent candidate scaffold.

    The meta-agent writes target_agent.py into cand_dir, which is then executed and
    graded so the candidate carries a verifier accuracy for selection.
    """
    os.makedirs(cand_dir, exist_ok=True)
    meta_prompt = build_meta_prompt(
        task_files,
        task_model,
        cand_dir,
        provider=target_provider,
        change_library_enabled=env_config.CHANGE_LIBRARY,
    )
    # Salvage-on-authoring-error: a meta-agent that hits max_turns / an API error may
    # already have written a working scaffold. Never let that abort the candidate — fall
    # through to run + evaluate whatever exists (yielding a None-accuracy record if nothing
    # usable was written). This makes _run_meta_candidate non-raising.
    try:
        asyncio.run(
            run_agent(
                model_name=meta_profile.model,
                max_turns=str(env_config.DEFAULT_MAX_TURNS),
                prompt=meta_prompt,
                agent_working_directory=cand_dir,
                agent_impl=meta_profile.agent_impl,
                provider=meta_profile.provider,
                protected_paths=protected_paths,
            )
        )
    except Exception as e:
        logger.error(
            "candidate %s authoring error (salvaging any scaffold): %s",
            os.path.basename(cand_dir),
            e,
        )
    target_agent_path = os.path.join(cand_dir, Names.TARGET_AGENT)
    stdout_log_file = os.path.join(cand_dir, Names.STDOUT_LOG)
    _run_target_agent(
        venv_dir=venv_dir,
        target_agent_path=target_agent_path,
        abs_dataset_dir=abs_dataset_dir,
        gen_dir=cand_dir,
        stdout_log_file=stdout_log_file,
        sandbox=sandbox,
        env_config=env_config,
    )
    run_evaluation(cand_dir, dataset_dir, venv_dir, config=env_config)
    return _make_candidate_record(cand_dir)


def _run_candidates_concurrently(
    factories: list[Callable[[], Candidate]],
    *,
    concurrency: int,
    stagger_seconds: float,
) -> list[Candidate]:
    """Run K best-of-N candidate factories, returning their Candidates in index order.

    Each factory is a zero-arg closure that produces, runs, and evaluates one candidate
    (binding its own `cand_dir`). Because candidate work is I/O-bound (subprocess + API
    waits release the GIL) and each factory wraps its authoring in `asyncio.run(...)` in
    a fresh per-thread event loop, threads are the right tool here — and the existing sync
    candidate functions stay untouched.

    Concurrency semantics:
      - `concurrency <= 0` -> run all K in parallel.
      - effective == 1     -> sequential plain loop (no executor); preserves today's exact
                              behavior and is the debug/escape-hatch path.
      - otherwise          -> ThreadPoolExecutor(max_workers=effective), staggering each
                              submission by `stagger_seconds` + uniform jitter to desync
                              API bursts (anti-thundering-herd).

    Results are gathered into a fixed-size list by index, so `select_best`'s deterministic
    tie-break is identical whether run sequentially or concurrently — completion order
    never leaks into the result ordering. The candidate factories are already non-raising;
    a future that still raises is caught here (defense-in-depth) and recorded as a
    None-accuracy candidate rather than aborting the whole gather.

    Raises RuntimeError only when EVERY candidate produced no scorable scaffold (all
    accuracies None) — the single fatal case, replacing "any candidate error kills the run".
    """
    n = len(factories)
    effective = n if concurrency <= 0 else min(concurrency, n)

    results: list[Candidate | None] = [None] * n

    if effective == 1:
        for i, factory in enumerate(factories):
            results[i] = factory()
    else:
        with ThreadPoolExecutor(max_workers=effective) as pool:
            future_to_index: dict = {}
            for i, factory in enumerate(factories):
                if i > 0 and stagger_seconds > 0:
                    time.sleep(stagger_seconds + random.uniform(0, stagger_seconds))
                future_to_index[pool.submit(factory)] = i
            for future in as_completed(future_to_index):
                i = future_to_index[future]
                try:
                    results[i] = future.result()
                except Exception as e:
                    logger.error("candidate cand_%d gather error (recording as unscored): %s", i, e)
                    results[i] = Candidate(name=f"cand_{i}", accuracy=None, code_size=0)

    candidates = [
        c if c is not None else Candidate(name=f"cand_{i}", accuracy=None, code_size=0) for i, c in enumerate(results)
    ]

    if all(c.accuracy is None for c in candidates):
        raise RuntimeError(f"All {n} best-of-N candidates failed to produce a scored scaffold")

    return candidates


def _run_gen1_best_of_n(
    run_setup: RunSetup,
    task_files: TaskFiles,
    task_model: str,
    meta_profile: MetaAgentProfile,
    abs_dataset_dir: str,
    dataset_dir: str,
    sandbox: str,
    env_config: Config,
    target_provider: Provider,
    protected_paths: list[str] | None = None,
) -> None:
    """Produce K gen-1 meta-agent candidates, then promote the verifier-best to gen_1/.

    Only entered when BEST_OF_N > 1 (harness mode); the K=1 path stays in main() unchanged.
    Candidates run concurrently (subject to BEST_OF_N_CONCURRENCY), gathered in index order
    so selection stays deterministic. The winner's artifacts are copied up into gen_1/ so the
    main loop (which reads gen_1/) is unchanged.
    """
    gen1_dir = run_setup.meta_agent_working_directory

    def _make_factory(i: int) -> Callable[[], Candidate]:
        cand_dir = os.path.join(gen1_dir, f"cand_{i}")

        def _factory() -> Candidate:
            candidate = _run_meta_candidate(
                cand_dir=cand_dir,
                task_files=task_files,
                task_model=task_model,
                meta_profile=meta_profile,
                venv_dir=run_setup.venv_dir,
                abs_dataset_dir=abs_dataset_dir,
                dataset_dir=dataset_dir,
                sandbox=sandbox,
                env_config=env_config,
                target_provider=target_provider,
                protected_paths=protected_paths,
            )
            logger.info(f"  Best-of-N gen-1 candidate {candidate.name}: accuracy={candidate.accuracy}")
            return candidate

        return _factory

    factories = [_make_factory(i) for i in range(env_config.BEST_OF_N)]
    candidates = _run_candidates_concurrently(
        factories,
        concurrency=env_config.BEST_OF_N_CONCURRENCY,
        stagger_seconds=env_config.BEST_OF_N_STAGGER_SECONDS,
    )

    winner = select_best(
        candidates,
        selection=env_config.BEST_OF_N_SELECTION,
        tiebreak=env_config.BEST_OF_N_TIEBREAK,
    )
    logger.info(f"  Best-of-N gen-1 winner: {winner.name} (accuracy={winner.accuracy})")
    _promote_candidate(os.path.join(gen1_dir, winner.name), gen1_dir)


def _select_base_generation(context_mgr: ContextManager, current_gen: int, base_on_best: bool) -> int:
    """Choose which generation the feedback agent should build on.

    When `base_on_best` is on and a best-so-far generation exists, return its number so
    the next generation evolves from the strongest agent rather than the most recent.
    Otherwise (knob off, or no scored generation yet) return `current_gen` — today's
    behavior.
    """
    if not base_on_best:
        return current_gen
    best = context_mgr.best_generation()
    if best is None:
        return current_gen
    return best["gen_num"]


def _is_regression(context_mgr: ContextManager, current_gen: int) -> bool:
    """True when the current generation scored below the best earlier generation.

    Compares the current generation's accuracy against the best accuracy among strictly
    earlier generations. Returns False when either side lacks a parseable accuracy (no
    evidence of regression) — graceful degradation.
    """
    current_acc = None
    best_prior = -float("inf")
    for g in context_mgr.generations:
        acc = parse_accuracy(g["metrics"].get("accuracy"))
        if acc is None:
            continue
        if g["gen_num"] == current_gen:
            current_acc = acc
        elif g["gen_num"] < current_gen:
            best_prior = max(best_prior, acc)
    if current_acc is None or best_prior == -float("inf"):
        return False
    return current_acc < best_prior


def _run_feedback_candidate(
    cand_dir: str,
    current_gen: int,
    max_gen: int,
    run_dir: str,
    task_files: TaskFiles,
    execution_status: str,
    execution_section: str,
    meta_profile: MetaAgentProfile,
    venv_dir: str,
    abs_dataset_dir: str,
    dataset_dir: str,
    sandbox: str,
    env_config: Config,
    task_model: str,
    target_provider: Provider,
    base_gen: int | None,
    resolved_ref: ResolvedAgentReference | None = None,
    protected_paths: list[str] | None = None,
) -> Candidate:
    """Produce + run + evaluate one gen>=2 feedback-agent candidate scaffold.

    The feedback agent evolves `base_gen`'s target_agent.py into a new one written into
    cand_dir, which is then executed and graded so the candidate carries a verifier
    accuracy for selection. Mirrors `_run_meta_candidate` for the gen-1 path.
    """
    # Salvage-on-authoring-error: a feedback agent that hits max_turns / an API error may
    # already have written an improved scaffold. Never let that abort the candidate — fall
    # through to run + evaluate whatever exists (yielding a None-accuracy record if nothing
    # usable was written). This makes _run_feedback_candidate non-raising.
    try:
        _run_feedback_agent(
            current_gen=current_gen,
            max_gen=max_gen,
            run_dir=run_dir,
            next_gen_dir=cand_dir,
            task_files=task_files,
            execution_status=execution_status,
            execution_section=execution_section,
            meta_profile=meta_profile,
            env_config=env_config,
            dataset_dir=dataset_dir,
            task_model=task_model,
            target_provider=target_provider,
            resolved_ref=resolved_ref,
            base_gen=base_gen,
        )
    except Exception as e:
        logger.error(
            "candidate %s authoring error (salvaging any scaffold): %s",
            os.path.basename(cand_dir),
            e,
        )
    target_agent_path = os.path.join(cand_dir, Names.TARGET_AGENT)
    stdout_log_file = os.path.join(cand_dir, Names.STDOUT_LOG)
    _run_target_agent(
        venv_dir=venv_dir,
        target_agent_path=target_agent_path,
        abs_dataset_dir=abs_dataset_dir,
        gen_dir=cand_dir,
        stdout_log_file=stdout_log_file,
        sandbox=sandbox,
        env_config=env_config,
    )
    run_evaluation(cand_dir, dataset_dir, venv_dir, config=env_config)
    return _make_candidate_record(cand_dir)


def _run_gen_n_best_of_n(
    current_gen: int,
    max_gen: int,
    run_setup: RunSetup,
    next_gen_dir: str,
    task_files: TaskFiles,
    execution_status: str,
    execution_section: str,
    meta_profile: MetaAgentProfile,
    abs_dataset_dir: str,
    dataset_dir: str,
    sandbox: str,
    env_config: Config,
    task_model: str,
    target_provider: Provider,
    base_gen: int | None,
    resolved_ref: ResolvedAgentReference | None = None,
    protected_paths: list[str] | None = None,
) -> None:
    """Produce K feedback-agent candidates, then promote the verifier-best to next_gen_dir.

    Only entered when BEST_OF_N > 1 (harness mode); the K=1 path stays in run_generation
    unchanged. Candidates run concurrently (subject to BEST_OF_N_CONCURRENCY), gathered in
    index order so selection stays deterministic. Because selecting the best of K already
    rejects a bad draw, this subsumes reject-regression re-prompting (no extra attempt loop).
    Base-on-best still chooses which generation each candidate evolves from (`base_gen`). The
    winner's artifacts are copied up into next_gen_dir so the main loop (which reads
    gen_{next}/) is unchanged.
    """

    def _make_factory(i: int) -> Callable[[], Candidate]:
        cand_dir = os.path.join(next_gen_dir, f"cand_{i}")

        def _factory() -> Candidate:
            os.makedirs(cand_dir, exist_ok=True)
            candidate = _run_feedback_candidate(
                cand_dir=cand_dir,
                current_gen=current_gen,
                max_gen=max_gen,
                run_dir=run_setup.run_directory,
                task_files=task_files,
                execution_status=execution_status,
                execution_section=execution_section,
                meta_profile=meta_profile,
                venv_dir=run_setup.venv_dir,
                abs_dataset_dir=abs_dataset_dir,
                dataset_dir=dataset_dir,
                sandbox=sandbox,
                env_config=env_config,
                task_model=task_model,
                target_provider=target_provider,
                base_gen=base_gen,
                resolved_ref=resolved_ref,
                protected_paths=protected_paths,
            )
            logger.info(f"  Best-of-N gen-{current_gen + 1} candidate {candidate.name}: accuracy={candidate.accuracy}")
            return candidate

        return _factory

    factories = [_make_factory(i) for i in range(env_config.BEST_OF_N)]
    candidates = _run_candidates_concurrently(
        factories,
        concurrency=env_config.BEST_OF_N_CONCURRENCY,
        stagger_seconds=env_config.BEST_OF_N_STAGGER_SECONDS,
    )

    winner = select_best(
        candidates,
        selection=env_config.BEST_OF_N_SELECTION,
        tiebreak=env_config.BEST_OF_N_TIEBREAK,
    )
    logger.info(f"  Best-of-N gen-{current_gen + 1} winner: {winner.name} (accuracy={winner.accuracy})")
    _promote_candidate(os.path.join(next_gen_dir, winner.name), next_gen_dir)


def _reuse_promoted_generation(gen_dir: str, stdout_log_file: str) -> tuple[bool, str, str, str]:
    """Reconstruct the run-status tuple for a best-of-N-promoted generation.

    When BEST_OF_N > 1 the generation's scaffold was already produced AND evaluated as a
    candidate, then promoted into gen_dir. Re-running + re-evaluating it here would discard
    best-of-N's selection (the chosen winner re-scored under model nondeterminism) and
    corrupt base-on-best ranking. So we reuse the promoted artifacts instead: success is
    inferred from the presence of a parseable results.json (evaluation completed), and the
    captured stdout is read back from the promoted log.
    """
    success = _read_candidate_accuracy(gen_dir) is not None
    target_agent_stdout = ""
    if os.path.exists(stdout_log_file):
        try:
            with open(stdout_log_file, encoding="utf-8") as f:
                target_agent_stdout = f.read()
        except OSError:
            target_agent_stdout = ""
    error_msg = "" if success else "Promoted generation has no parseable results.json"
    return success, target_agent_stdout, "", error_msg


def run_generation(
    current_gen: int,
    max_gen: int,
    run_setup: RunSetup,
    task_files: TaskFiles,
    abs_dataset_dir: str,
    dataset_dir: str,
    meta_profile: MetaAgentProfile,
    sandbox: str,
    env_config: Config,
    task_model: str,
    target_provider: Provider,
    focus: str = "harness",
    training_sandbox: str = "modal",
    resolved_ref: ResolvedAgentReference | None = None,
) -> None:
    """Execute one generation: run target agent, evaluate, optionally run feedback agent.

    Args:
        focus: "harness" for code improvement or "weights" for RL-based tuning
        training_sandbox: "modal" (default) or "sandboxfusion" for train.py code execution
    """
    run_dir = run_setup.run_directory
    layout = RunLayout(run_dir)
    gen_dir = layout.gen_dir(current_gen)

    # Best-of-N (BEST_OF_N > 1) is harness-only; weights mode keeps the single-candidate path.
    best_of_n_active = env_config.BEST_OF_N > 1 and focus == "harness"

    # Use train.py for weights mode (RL tuning), target_agent.py for harness mode
    target_agent_path = os.path.join(gen_dir, "train.py") if focus == "weights" else layout.target_agent(current_gen)

    stdout_log_file = layout.stdout_log(current_gen, focus=focus)

    generation_start_time = time.time()

    if best_of_n_active:
        # This generation was already produced AND evaluated as a promoted best-of-N
        # candidate (gen-1 via _run_gen1_best_of_n, gen>=2 via the prior generation's
        # _run_gen_n_best_of_n). Re-running would discard the selected winner's accuracy
        # under model nondeterminism, so reuse the promoted artifacts instead.
        logger.info(f"Best-of-N: reusing promoted generation {current_gen} artifacts (skipping re-run + re-eval)")
        target_agent_success, target_agent_stdout, target_agent_stderr, target_agent_error_msg = (
            _reuse_promoted_generation(gen_dir, stdout_log_file)
        )
    else:
        logger.info(f"Running target agent: {target_agent_path}")
        logger.info(f"  → Stdout log: {stdout_log_file}")
        logger.info(f"  → Focus mode: {focus}")
        logger.info("=" * 60)

        # Install this generation's declared dependencies (if the agent wrote a
        # requirements.txt) before running the target agent.
        gen_requirements = os.path.join(gen_dir, Names.REQUIREMENTS_TXT)
        if os.path.isfile(gen_requirements):
            install_requirements(run_setup.venv_dir, gen_requirements)

        # Run target agent
        target_agent_success, target_agent_stdout, target_agent_stderr, target_agent_error_msg = _run_target_agent(
            venv_dir=run_setup.venv_dir,
            target_agent_path=target_agent_path,
            abs_dataset_dir=abs_dataset_dir,
            gen_dir=gen_dir,
            stdout_log_file=stdout_log_file,
            sandbox=sandbox,
            env_config=env_config,
        )

        # Run evaluation (if evaluate.py exists)
        logger.info("=" * 60)
        logger.info("Running evaluation (if available)...")
        run_evaluation(gen_dir, dataset_dir, run_setup.venv_dir, config=env_config)
        logger.info("=" * 60)

    generation_duration = time.time() - generation_start_time

    # Add generation to context
    improvement_md_path = layout.improvement_md(current_gen)
    run_setup.context_mgr.add_generation(
        gen_num=current_gen,
        gen_data={
            "success": target_agent_success,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration": generation_duration,
            "agent_path": target_agent_path,
            "gen_dir": gen_dir,
            "improvement_path": improvement_md_path if os.path.exists(improvement_md_path) else None,
            "execution_type": "Multi-trajectory"
            if os.path.isdir(layout.agent_execution_dir(current_gen))
            else "Single",
        },
    )

    # Run feedback agent (if not the last generation)
    if current_gen < max_gen:
        logger.info(f"Running feedback agent for generation {current_gen}")
        logger.info("Loading agent execution log...")

        execution_status, execution_section = _build_feedback_context(
            current_gen=current_gen,
            gen_dir=gen_dir,
            dataset_dir=dataset_dir,
            target_agent_success=target_agent_success,
            target_agent_error_msg=target_agent_error_msg,
            target_agent_stdout=target_agent_stdout,
            target_agent_stderr=target_agent_stderr,
            stdout_log_file=stdout_log_file,
            task_files=task_files,
            config=env_config,
        )

        next_gen = current_gen + 1
        next_gen_directory = layout.gen_dir(next_gen)

        # Base-on-best (harness-only): evolve from the strongest generation so far rather
        # than the most recent. In weights mode base_gen stays current_gen (no-op routing).
        context_mgr = run_setup.context_mgr
        base_gen = current_gen
        if focus == "harness":
            base_gen = _select_base_generation(context_mgr, current_gen, env_config.BASE_ON_BEST)
            if base_gen != current_gen:
                logger.info(f"  Base-on-best: evolving from generation {base_gen} (best so far), not {current_gen}")

        if not best_of_n_active:
            # K==1 (or weights mode): reject-regression may give the feedback agent extra
            # attempts when the current generation scored below an earlier one. Disabled
            # knob -> a single attempt (byte-identical to upstream).
            max_attempts = 1
            if focus == "harness" and env_config.REJECT_REGRESSION and _is_regression(context_mgr, current_gen):
                max_attempts += max(0, env_config.REGRESSION_REPROMPT_MAX)
                logger.info(
                    f"  Reject-regression: generation {current_gen} regressed; up to {max_attempts} feedback attempts"
                )

            feedback_base_gen = base_gen if focus == "harness" else None
            for attempt in range(max_attempts):
                if attempt > 0:
                    logger.info(f"  Reject-regression re-prompt attempt {attempt + 1}/{max_attempts}")
                _run_feedback_agent(
                    current_gen=current_gen,
                    max_gen=max_gen,
                    run_dir=run_dir,
                    next_gen_dir=next_gen_directory,
                    task_files=task_files,
                    execution_status=execution_status,
                    execution_section=execution_section,
                    meta_profile=meta_profile,
                    env_config=env_config,
                    dataset_dir=dataset_dir,
                    task_model=task_model,
                    target_provider=target_provider,
                    focus=focus,
                    resolved_ref=resolved_ref,
                    base_gen=feedback_base_gen,
                )
        else:
            # Best-of-N (K>1, harness): sample K feedback candidates and promote the
            # verifier-best. Selecting the best of K already rejects regressors, so the
            # reject-regression re-prompt loop is subsumed; base-on-best still chooses
            # each candidate's base generation.
            _run_gen_n_best_of_n(
                current_gen=current_gen,
                max_gen=max_gen,
                run_setup=run_setup,
                next_gen_dir=next_gen_directory,
                task_files=task_files,
                execution_status=execution_status,
                execution_section=execution_section,
                meta_profile=meta_profile,
                abs_dataset_dir=abs_dataset_dir,
                dataset_dir=dataset_dir,
                sandbox=sandbox,
                env_config=env_config,
                task_model=task_model,
                target_provider=target_provider,
                base_gen=base_gen,
                resolved_ref=resolved_ref,
            )
    else:
        logger.info(f"Generation {current_gen} is the final generation. Skipping feedback agent.")


def _run_web(args) -> None:
    """Dispatch for ``sia web``: serve the runs visualizer in the foreground."""
    configure_logging(args.log_level)
    from sia.web import serve

    serve(host=args.host, port=args.port, runs_dir=args.runs_dir, open_browser=not args.no_browser)


def main():
    configure_logging()
    _print_welcome()

    # Load env-var overrides (lower priority than explicit CLI flags)
    env_config = Config.from_env()

    # Parse command-line arguments
    args = cli.parse_args(env_config)

    if args.command == "web":
        _run_web(args)
        return

    # Apply CLI log level (overrides the import-time default / $SIA_LOG_LEVEL).
    configure_logging(args.log_level)

    # Start the live dashboard in a background thread so the run is watchable.
    if not args.no_web:
        from sia.web import serve_in_background

        serve_in_background(host=args.web_host, port=args.web_port, runs_dir=Names.RUNS_ROOT)

    max_gen = args.max_gen
    task_dir, shared_dir = resolve_task_dir(args.task, args.task_dir)
    run_id = args.run_id

    # Resolve agent profiles: the meta profile bundles agent_impl + model + provider;
    # the target profile bundles model + provider + agent_reference (the seed code).
    meta_profile = load_meta_agent_profile(args.meta_agent_profile)
    target_profile = load_target_agent_profile(args.target_agent_profile)
    meta_model = meta_profile.model
    task_model = target_profile.model
    agent_impl = meta_profile.agent_impl
    target_provider = target_profile.provider

    task_layout = TaskLayout(task_dir, shared_dir)
    resolved_ref = resolve_agent_reference(target_profile.agent_reference, task_layout)

    logger.info("Configuration:")
    logger.info(f"  - Maximum generations: {max_gen}")
    logger.info(f"  - Task directory: {task_dir}")
    logger.info(f"  - Run ID: {run_id}")
    logger.info(
        f"  - Meta agent profile: {meta_profile.profile_id} (agent_impl={agent_impl}, model={meta_model}, "
        f"provider={meta_profile.provider.provider_id})"
    )
    logger.info(
        f"  - Target agent profile: {target_profile.profile_id} (model={task_model}, "
        f"provider={target_provider.provider_id}/{target_provider.client_kind}, "
        f"reference={target_profile.agent_reference.kind})"
    )

    for label, prov in (("meta", meta_profile.provider), ("target", target_provider)):
        if not os.getenv(prov.api_key_env):
            logger.warning(f"  ⚠ {prov.api_key_env} is not set; the {label} agent may fail to authenticate.")

    # Check for required API keys when using weights mode
    if args.focus == "weights":
        # TINKER_API_KEY is always required for weights mode
        if not os.getenv("TINKER_API_KEY"):
            logger.error("✗ TINKER_API_KEY environment variable is required for weights mode (RL-based tuning).")
            raise RuntimeError(
                "TINKER_API_KEY not set. Please set the TINKER_API_KEY environment variable to use weights mode."
            )

        # MODAL_API_KEY is required if using modal sandbox
        if args.training_sandbox == "modal" and not os.getenv("MODAL_TOKEN_ID"):
            logger.error("✗ MODAL_TOKEN_ID environment variable is required when training_sandbox='modal'.")
            raise RuntimeError(
                "MODAL_TOKEN_ID not set. Please set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET for Modal authentication."
            )

        # Warn about resource requirements for sandboxfusion
        if args.training_sandbox == "sandboxfusion":
            logger.warning(
                "⚠ Using SandboxFusion sandbox. Ensure at least 40GB+ free disk space is available for Docker."
            )
            logger.warning(
                "⚠ Make sure SandboxFusion is running on localhost:8080 or set SANDBOX_URL environment variable."
            )

    # ========================
    # SECTION 1: Load Files from Task Directory
    # ========================

    task_files = load_task_files(task_dir, shared_dir, resolved_ref)

    # ========================
    # SECTION 2: Setup Run Directories
    # ========================

    run_setup = setup_run_directory(
        run_id,
        task_dir,
        meta_model,
        task_model,
        agent_impl,
        max_gen,
        focus=args.focus,
        config=env_config,
        meta_profile=meta_profile,
        target_profile=target_profile,
    )

    # ========================
    # SECTION 3: Build Initial Prompt
    # ========================

    # A multi-file directory reference is read by the agent from disk (copied into its
    # working dir) rather than embedded in the prompt.
    copy_reference_into(resolved_ref, run_setup.meta_agent_working_directory)
    reference_dir = run_setup.meta_agent_working_directory if resolved_ref.ref_dir is not None else None

    # Log focus mode and training sandbox
    logger.info("Configuration (continued):")
    logger.info(f"  - Focus mode: {args.focus}")
    if args.focus == "weights":
        logger.info(f"  - Training sandbox (for train.py code execution): {args.training_sandbox}")

    # Build meta prompt based on focus mode (weights=RL tuning, harness=code improvement)
    meta_agent_prompt = build_meta_prompt(
        task_files,
        task_model,
        run_setup.meta_agent_working_directory,
        provider=target_provider,
        reference_dir=reference_dir,
        focus=args.focus,
        training_sandbox=args.training_sandbox,
        change_library_enabled=env_config.CHANGE_LIBRARY,
    )

    # ========================
    # SECTION 4: Run Target Agent Creation (Meta-Agent)
    # ========================

    # Save the meta-agent prompt for debugging/transparency
    meta_agent_prompt_path = os.path.join(run_setup.meta_agent_working_directory, Names.META_PROMPT)
    write_text(meta_agent_prompt_path, meta_agent_prompt)
    logger.info(f"  ✓ Saved meta-agent prompt to: {meta_agent_prompt_path}")

    # Best-of-N (BEST_OF_N > 1) is harness-only; weights mode keeps the single-candidate path.
    if env_config.BEST_OF_N > 1 and args.focus == "harness":
        logger.info(f"Best-of-N: producing {env_config.BEST_OF_N} gen-1 meta-agent candidates")
        _run_gen1_best_of_n(
            run_setup=run_setup,
            task_files=task_files,
            task_model=task_model,
            meta_profile=meta_profile,
            abs_dataset_dir=task_layout.abs_dataset_dir,
            dataset_dir=task_layout.dataset_dir,
            sandbox=args.sandbox,
            env_config=env_config,
            target_provider=target_provider,
            protected_paths=compute_protected_paths(task_dir),
        )
    else:
        asyncio.run(
            run_agent(
                model_name=meta_model,
                max_turns=str(env_config.DEFAULT_MAX_TURNS),
                prompt=meta_agent_prompt,
                agent_working_directory=run_setup.meta_agent_working_directory,
                agent_impl=agent_impl,
                provider=meta_profile.provider,
                protected_paths=compute_protected_paths(task_dir),
            )
        )

    # ========================
    # SECTION 5: Main Loop - Run Target Agent and Feedback Agent
    # ========================

    dataset_directory = task_layout.dataset_dir
    abs_dataset_directory = task_layout.abs_dataset_dir
    logger.info(f"Dataset directory: {abs_dataset_directory}")

    for current_gen in range(1, max_gen + 1):
        logger.info("=" * 80)
        logger.info(f"Starting Generation {current_gen} of {max_gen}")
        logger.info("=" * 80)

        run_generation(
            current_gen=current_gen,
            max_gen=max_gen,
            run_setup=run_setup,
            task_files=task_files,
            abs_dataset_dir=abs_dataset_directory,
            dataset_dir=dataset_directory,
            meta_profile=meta_profile,
            sandbox=args.sandbox,
            env_config=env_config,
            task_model=task_model,
            target_provider=target_provider,
            focus=args.focus,
            training_sandbox=args.training_sandbox,
            resolved_ref=resolved_ref,
        )

        # Early stopping for weights mode: if feedback agent signaled completion
        if args.focus == "weights" and current_gen < max_gen:
            next_gen = current_gen + 1
            next_gen_dir = RunLayout(run_setup.run_directory).gen_dir(next_gen)
            if os.path.exists(os.path.join(next_gen_dir, "COMPLETED")):
                logger.info("Feedback agent signaled completion via COMPLETED file. Exiting evolution loop early.")
                break

    # Finalize context with summary statistics
    logger.info("Finalizing context.md with summary statistics...")
    run_setup.context_mgr.finalize()

    logger.info("=" * 80)
    logger.info(f"Orchestrator completed all {max_gen} generations successfully!")
    logger.info(f"Results saved in: {run_setup.run_directory}")
    logger.info(f"Context summary: {os.path.join(run_setup.run_directory, Names.CONTEXT_MD)}")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
