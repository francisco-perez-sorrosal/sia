"""Centralized configuration for SIA framework."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import ClassVar

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off"})


def _to_bool(value: str) -> bool:
    """Parse an env-var string into a bool. Raises ValueError on unrecognized input."""
    normalized = value.strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    raise ValueError(f"Cannot interpret {value!r} as a boolean")


def _to_str_tuple(value: str) -> tuple[str, ...]:
    """Parse a comma-separated env-var string into a tuple of trimmed strings."""
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass
class Config:
    """Single source of truth for all SIA configuration defaults."""

    # Agent profile defaults (JSON profiles selected on the CLI, see sia/defaults/profiles/)
    DEFAULT_META_AGENT_PROFILE: str = "default-meta"
    DEFAULT_TARGET_AGENT_PROFILE: str = "default-target"

    # Model defaults (fallbacks for context metadata / env overrides)
    DEFAULT_CLAUDE_META_MODEL: str = "haiku"
    DEFAULT_OPENHANDS_META_MODEL: str = "gemini/gemini-3.1-pro-preview"
    DEFAULT_TASK_MODEL: str = "claude-haiku-4-5-20251001"

    # Generation defaults
    DEFAULT_MAX_GENERATIONS: int = 3
    DEFAULT_RUN_ID: int = 1

    # Agent execution
    DEFAULT_MAX_TURNS: int = 20
    CONTEXT_SUMMARY_MAX_TURNS: int = 5
    DEFAULT_AGENT_IMPL: str = "claude"

    # Truncation limits
    AGENT_CODE_PREVIEW_LIMIT: int = 3000
    TRAJECTORY_PREVIEW_LIMIT: int = 1000
    TOOL_RESULT_PREVIEW_LIMIT: int = 500
    INSIGHT_PREVIEW_LIMIT: int = 200

    # Timeouts
    SHELL_TIMEOUT: int = 30
    EVAL_TIMEOUT: int = 600

    # Sandbox settings
    SANDBOX_MODE: str = "none"  # "none" or "docker"
    DOCKER_IMAGE: str = "python:3.11-slim"
    DOCKER_MEMORY_LIMIT: str = "2g"
    DOCKER_CPU_LIMIT: float = 2.0
    DOCKER_TIMEOUT: int = 3600  # seconds

    # File size limits (bytes)
    MAX_CONTEXT_FILE_SIZE: int = 10_000_000  # 10 MB
    MAX_EXECUTION_LOG_SIZE: int = 50_000_000  # 50 MB

    # Harness-amplification knobs (each defaults to today's behavior; turning all of
    # them off reproduces the pre-amplification pipeline bit-for-bit). Harness-mode only.
    # Lever B — failure-taxonomy digest injected into the feedback prompt.
    FAILURE_TAXONOMY: bool = True
    FAILURE_TAXONOMY_TOP_N: int = 5
    # Pass-set for the verifier->feedback contract: a grader item whose `status` is
    # not in this set counts as a failure.
    VERIFIER_PASS_STATUSES: tuple[str, ...] = ("CORRECT", "PASS", "correct")
    # Cap on the number of FAILED held-out questions surfaced (gold-free) in the
    # feedback prompt's curated eval summary. Diversified across status and group.
    FEEDBACK_FAILURE_SAMPLES: int = 20
    # Lever C — front-load a task-family change library into the meta-agent prompt.
    CHANGE_LIBRARY: bool = True
    CHANGE_LIBRARY_PATH: str = "reference/change_library.md"
    # Lever E — base the next generation on the best-so-far and reject regressions.
    BASE_ON_BEST: bool = True
    REJECT_REGRESSION: bool = True
    REGRESSION_REPROMPT_MAX: int = 1
    # Allow the feedback agent to specialize to the task family (not held-out items).
    ALLOW_TASK_FAMILY_SPECIALIZATION: bool = True
    # Lever A — verifier-guided best-of-N candidate scaffolds per generation.
    # BEST_OF_N == 1 is OFF: a single candidate, byte-identical to today's loop.
    BEST_OF_N: int = 1
    BEST_OF_N_SELECTION: str = "accuracy"
    BEST_OF_N_TIEBREAK: str = "smaller_code"
    # Concurrency for best-of-N candidate authoring. 0 -> run all K in parallel;
    # 1 -> sequential (today's behavior / debug escape hatch); >1 -> min(knob, K).
    BEST_OF_N_CONCURRENCY: int = 0
    # Per-submission stagger (seconds, + uniform jitter) to desynchronize API bursts
    # when candidates launch concurrently (anti-thundering-herd). Tests set 0.
    BEST_OF_N_STAGGER_SECONDS: float = 1.0

    # Virtual environment packages.
    VENV_PACKAGES: ClassVar[list[str]] = [
        "anthropic",
        "openai",
        "python-dotenv",
        "google-genai",
        "claude-agent-sdk",
        "tqdm",
        "pydantic",
        "scikit-learn",
        "pandas",
        "numpy",
    ]

    # Additional packages only for weights mode (RL training)
    WEIGHTS_VENV_PACKAGES: ClassVar[list[str]] = [
        "vllm",
        "tinker",
        "tinker-cookbook[modal] @ git+https://github.com/thinking-machines-lab/tinker-cookbook.git@nightly",
    ]

    @classmethod
    def from_env(cls) -> Config:
        """Create Config with overrides from SIA_* environment variables."""
        cfg = cls()
        env_map = {
            "SIA_META_AGENT_PROFILE": ("DEFAULT_META_AGENT_PROFILE", str),
            "SIA_TARGET_AGENT_PROFILE": ("DEFAULT_TARGET_AGENT_PROFILE", str),
            "SIA_META_MODEL": ("DEFAULT_CLAUDE_META_MODEL", str),
            "SIA_TASK_MODEL": ("DEFAULT_TASK_MODEL", str),
            "SIA_MAX_GENERATIONS": ("DEFAULT_MAX_GENERATIONS", int),
            "SIA_AGENT_IMPL": ("DEFAULT_AGENT_IMPL", str),
            "SIA_MAX_TURNS": ("DEFAULT_MAX_TURNS", int),
            "SIA_SANDBOX_MODE": ("SANDBOX_MODE", str),
            "SIA_FAILURE_TAXONOMY": ("FAILURE_TAXONOMY", _to_bool),
            "SIA_FAILURE_TAXONOMY_TOP_N": ("FAILURE_TAXONOMY_TOP_N", int),
            "SIA_VERIFIER_PASS_STATUSES": ("VERIFIER_PASS_STATUSES", _to_str_tuple),
            "SIA_FEEDBACK_FAILURE_SAMPLES": ("FEEDBACK_FAILURE_SAMPLES", int),
            "SIA_CHANGE_LIBRARY": ("CHANGE_LIBRARY", _to_bool),
            "SIA_CHANGE_LIBRARY_PATH": ("CHANGE_LIBRARY_PATH", str),
            "SIA_BASE_ON_BEST": ("BASE_ON_BEST", _to_bool),
            "SIA_REJECT_REGRESSION": ("REJECT_REGRESSION", _to_bool),
            "SIA_REGRESSION_REPROMPT_MAX": ("REGRESSION_REPROMPT_MAX", int),
            "SIA_ALLOW_TASK_FAMILY_SPECIALIZATION": ("ALLOW_TASK_FAMILY_SPECIALIZATION", _to_bool),
            "SIA_BEST_OF_N": ("BEST_OF_N", int),
            "SIA_BEST_OF_N_SELECTION": ("BEST_OF_N_SELECTION", str),
            "SIA_BEST_OF_N_TIEBREAK": ("BEST_OF_N_TIEBREAK", str),
            "SIA_BEST_OF_N_CONCURRENCY": ("BEST_OF_N_CONCURRENCY", int),
            "SIA_BEST_OF_N_STAGGER_SECONDS": ("BEST_OF_N_STAGGER_SECONDS", float),
        }
        for env_var, (attr, converter) in env_map.items():
            val = os.environ.get(env_var)
            if val is not None:
                with contextlib.suppress(ValueError, TypeError):
                    setattr(cfg, attr, converter(val))
        return cfg
