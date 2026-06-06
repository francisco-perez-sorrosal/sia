# Architecture Guide

<!-- Developer navigation guide. Every component name and file path here is verified against the
     codebase on the last-verified date. For design rationale, constraints, and known gaps, see
     .ai-state/DESIGN.md (the architect-facing companion). -->

## 1. Overview

| Attribute | Value |
|-----------|-------|
| **System** | SIA — Self-Improving AI framework (`sia-agent`) |
| **Type** | CLI tool / research framework (PyPI library) |
| **Language / Framework** | Python 3.11+ (setuptools) |
| **Architecture pattern** | Single-process orchestrator driving subprocess-isolated agents |
| **Last verified against code** | 2026-06-05 |

SIA runs a *self-improving agent loop*. A Meta-Agent generates a task-specific `target_agent.py`; the
Target Agent runs the task and logs a trajectory; a Feedback Agent analyzes that trajectory and
rewrites the agent for the next generation. The loop repeats for `--max_gen` generations, and a
Context Manager records the evolution into `runs/run_N/context.md`. Meta/feedback agents run through a
pluggable backend (`claude` or `openhands`); the target agent runs as a separate OS subprocess,
optionally inside a network-isolated Docker container.

## 2. System Context

![SIA System Context (L0) — Researcher invokes the SIA Orchestrator from the CLI; the orchestrator calls an external Agent Backend LLM (Claude or OpenHands) for meta/feedback agents and, in sandbox mode, runs the target agent inside Docker](diagrams/architecture/rendered/context.svg)

<details>
<summary>LikeC4 source (edit diagrams/architecture/src/architecture.c4 to update this diagram)</summary>

```c4
view context of sia {
  title "SIA — System Context (L0)"
  include user, sia, llm, docker
}
```

</details>

> **Component detail:** [Components](#3-components)

## 3. Components

![SIA Components (L1) — Orchestrator depends on Context Manager, Agent Runner, Config and Bundled Tasks; Context Manager depends on Agent Runner and Config; Agent Runner depends on Config](diagrams/architecture/rendered/components.svg)

<details>
<summary>LikeC4 source (edit diagrams/architecture/src/architecture.c4 to update this diagram)</summary>

```c4
view components of sia {
  title "SIA — Components (L1)"
  include *
}
```

</details>

<!-- aac:generated source=docs/diagrams/architecture/src/architecture.c4 view=components last-regen=2026-06-05 -->

| Component | Responsibility | Key Files |
|-----------|---------------|-----------|
| Orchestrator | CLI entry, run setup (dir + per-run venv), prompt assembly, generation loop, target-agent and evaluation subprocesses | `sia/orchestrator.py`, `sia/__main__.py` |
| Context Manager | Maintains `runs/run_N/context.md`; metric extraction, code-growth deltas, LLM diff summaries | `sia/context_manager.py` |
| Agent Runner | Backend dispatch (`claude` in-process / `openhands`) for meta and feedback agents | `sia/util.py` |
| Config | Defaults + `SIA_*` env overrides; venv package set, timeouts, size/truncation limits, Docker settings | `sia/config.py` |
| Bundled Tasks | Per-task `task.md`, `reference_target_agent.py`, `SAMPLE_TASK_DESCRIPTIONS.md`, `evaluate.py`, plus `_shared/` samples (read as text) | `sia/tasks/` |
| MLE-Bench dataset prep | Standalone utility to build a task dir from an MLE-Bench competition (not part of the loop) | `sia/prepare_mlebench_dataset.py` |

<!-- aac:end -->

**Where to start reading:** entry point is `sia/orchestrator.py:main`. The loop body is
`run_generation`; the two prompt builders are `build_meta_prompt` and `build_feedback_prompt`.
Backend calls funnel through `sia/util.py:run_agent`.

## 4. Interfaces

| Interface | Type | Provider | Consumer(s) | Contract |
|-----------|------|----------|-------------|----------|
| `sia` CLI | Process args | Orchestrator | Researcher | `--task`/`--task_dir` (required, exclusive), `--max_gen`, `--run_id`, `--meta_model`, `--task_model`, `--backend`, `--sandbox` (`sia/orchestrator.py:main`) |
| `run_agent(...)` | Async function | Agent Runner | Orchestrator, Context Manager | Dispatches by backend; `ValueError` on unknown backend (`sia/util.py`) |
| `ContextManager.{initialize,add_generation,finalize}` | Methods | Context Manager | Orchestrator | Generation lifecycle (`sia/context_manager.py`) |
| `evaluate.py --gen-dir <dir>` | Process args | Bundled task | Orchestrator | Optional; emits `results.json` (`sia/orchestrator.py:run_evaluation`) |

## 5. Data Flow

Data flows are diagrammed in [`.ai-state/DESIGN.md` §5](../.ai-state/DESIGN.md#5-data-flow). To trace
a run: start at `sia/orchestrator.py:main` → `setup_run_directory` (creates `runs/run_N/` + venv) →
meta-agent bootstrap → the `for current_gen in range(...)` loop calling `run_generation` → each
generation runs the target agent (`_run_target_agent`), evaluates (`run_evaluation`), records
(`ContextManager.add_generation`), and (if not last) runs the feedback agent.

## 6. Dependencies

External dependencies, versions, and criticality are listed in
[`.ai-state/DESIGN.md` §7](../.ai-state/DESIGN.md#7-dependencies). Verified against `pyproject.toml`.

## 7. Constraints

System constraints (Python version, the one-way import spine, run-directory pre-existence guard,
sandbox enforcement boundary) are listed in
[`.ai-state/DESIGN.md` §8](../.ai-state/DESIGN.md#8-constraints).

## 8. Decisions

<!-- aac:authored owner=systems-architect last-reviewed=2026-06-05 -->

Architectural decisions are recorded as ADRs in [`.ai-state/decisions/`](../.ai-state/decisions/).
The canonical, auto-generated cross-reference is
[`DECISIONS_INDEX.md`](../.ai-state/decisions/DECISIONS_INDEX.md). For design-target rationale, see
[`.ai-state/DESIGN.md`](../.ai-state/DESIGN.md) — this guide does not summarize decisions inline.

<!-- aac:end -->
