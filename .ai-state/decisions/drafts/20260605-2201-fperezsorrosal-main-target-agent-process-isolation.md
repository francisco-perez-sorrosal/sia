---
id: dec-draft-bb4c2d5f
title: Target agents run as isolated OS subprocesses, never imported
status: accepted
category: architectural
date: 2026-06-05
summary: SIA generates target_agent.py and executes it as a separate OS subprocess (per-run venv or Docker), never importing it; the dataset-RO/working-RW contract is OS-enforced only under --sandbox docker.
tags: [isolation, subprocess, sandbox, security-boundary, agent-loop]
made_by: user
agent_type: systems-architect
branch: main
pipeline_tier: standard
affected_files:
  - sia/orchestrator.py
  - sia/config.py
---

## Context

This ADR is a retrospective record produced during a baseline architecture audit of an existing
codebase (no feature in scope). It captures a load-bearing invariant that is non-obvious from any
single file and easy to violate during future work.

SIA's self-improving loop produces `target_agent.py` files written by an LLM (the meta- and
feedback-agents). These generated files are untrusted, arbitrary Python. The architecture must run
them to obtain execution trajectories, while keeping the SIA process and the host filesystem safe.

## Decision

Generated target agents are **executed as separate OS subprocesses, never imported into the SIA
process**:

- Each run gets its own virtualenv (`uv venv` when available, else stdlib `venv`); the target agent
  runs via that venv's `python` (`sia/orchestrator.py:_run_target_agent`).
- The target agent receives its dataset and working directories **only** as command-line arguments
  (`--dataset_dir`, `--working_dir`); the read-only-dataset / read-write-working contract is conveyed
  to the generating LLM as prompt text in `build_meta_prompt`.
- OS-level enforcement of that contract exists **only** under `--sandbox docker`
  (`_run_target_agent_sandboxed`): `--network none`, dataset mounted `:ro`, working dir mounted `:rw`,
  memory/CPU limits from `Config`. The default `--sandbox none` provides process isolation but **no**
  filesystem-boundary enforcement.
- A target-agent crash is non-fatal to the loop — the orchestrator records a FAILED status and still
  runs the feedback agent so the next generation can repair the failure.

The corollary invariant: `sia/tasks/` reference agents are template *text* (excluded from `ruff` and
`ty`), never importable source. SIA source never imports anything it generates or ships as a task
template.

## Considered Options

### In-process execution (import and call the generated agent)

- Pros: simpler, faster, no venv/subprocess overhead.
- Cons: arbitrary LLM-generated code runs with SIA's privileges and can corrupt SIA state, leak the
  orchestrator's memory, or crash the whole loop. Rejected — unacceptable for untrusted code.

### Always-Docker execution

- Pros: strong, uniform filesystem + network isolation.
- Cons: hard dependency on a Docker daemon and a base image for every run; heavyweight for trusted
  local research use. Rejected as the default; offered as opt-in `--sandbox docker`.

### Subprocess in a per-run venv, Docker opt-in (chosen)

- Pros: process isolation and dependency isolation by default; strong OS-level sandbox available when
  the task source is untrusted; cheap for trusted local runs.
- Cons: the RO/RW contract is advisory (prompt-enforced) under the default mode — see Consequences.

## Consequences

**Positive**

- Untrusted generated code cannot corrupt the SIA process; a crash is contained and even feeds the
  next generation's repair.
- Per-run venv isolates the target agent's dependency set (`Config.VENV_PACKAGES`) from SIA's own.
- A clear, stable boundary: SIA never imports generated or task-template code.

**Negative / risk**

- Under `--sandbox none` (the default), a generated target agent can read and write anywhere the venv
  python can — the dataset-RO / working-RW contract is **not** OS-enforced. Running tasks from an
  untrusted source without `--sandbox docker` is a real security-boundary gap (recorded in
  `.ai-state/DESIGN.md` §10 Known Gaps).
- Subprocess + venv creation adds per-run latency versus in-process execution.

**Invariant to preserve:** future changes must not import generated `target_agent.py` or
`sia/tasks/*/reference/*` modules into the SIA process, and must not silently weaken the
`--sandbox docker` isolation flags (network none, dataset `:ro`).
