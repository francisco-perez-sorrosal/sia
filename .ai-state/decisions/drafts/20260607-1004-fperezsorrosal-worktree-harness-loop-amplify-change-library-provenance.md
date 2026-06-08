---
id: dec-draft-590509b7
title: Change-library provenance — hand-curated task-family knowledge for v1
status: proposed
category: architectural
date: 2026-06-07
summary: The M front-load change-library is a hand-curated per-task reference/change_library.md of task-family heuristics; auto-mining from prior runs deferred to a later phase.
tags: [harness-loop, change-library, meta-agent, front-load, adas, sia-h]
made_by: agent
agent_type: systems-architect
branch: worktree-harness-loop-amplify
pipeline_tier: standard
affected_files:
  - sia/orchestrator.py
  - sia/config.py
---

## Context

The captured runs rediscover the same front-load set over generations 2–4 — adequate token
budget, determinism (temperature=0), schema sample-rows, a core SQL-correctness rule pack, and
structured-checklist (not free-form CoT) output. The signals stream shows this set's *membership*
transfers across target models even though its *ranking* does not. Front-loading it into `M`'s
gen-1 prior (Self-Discover + ADAS curated-archive pattern) is the largest single compression
lever: gen-1 would start near the old gen-3 state. The open question (literature open-Q2) is
**provenance** — hand-curated from the demo's taxonomy, or auto-mined from prior runs'
`improvement.md` files (the "more self-improving" ADAS-archive path). Auto-mining is heavier and
risks two failure modes: leaking item-specific answers (gaming the held-out set — paper §8
coupled-Goodhart) and amplifying the existing SE-hygiene bias if it mines change *counts*.

## Decision

For v1, the change-library is a **hand-curated, per-task `reference/change_library.md`** file
holding **task-family heuristics only** (never item-specific answers). It is loaded as an
optional `TaskFiles` field (absent file → today's behavior) and composed by `M` into the gen-1
system prompt, gated by `CHANGE_LIBRARY` / `CHANGE_LIBRARY_PATH` config. **Auto-mining from
prior-run archives is explicitly deferred** to a later phase, behind the same config surface, so
the v1 file can later be machine-generated without a contract change. A review gate enforces the
task-family-not-item-specific rule.

## Considered Options

### A. Hand-curated task-family library, per-task file (CHOSEN for v1)
- Pros: simplest; auditable for the answer-leak guard; keeps SQL knowledge in a data file out of
  `sia/` core; immediately testable against the front-load-yield acceptance criterion.
- Cons: manual authoring per task; does not itself "self-improve."

### B. Auto-mined from prior `improvement.md` / context archives (ADAS archive)
- Pros: genuinely self-improving; library grows from the loop's own discoveries.
- Cons: heavier; high answer-leak / Goodhart risk; mining by change-count would inherit the
  misleading SE-vs-domain split (signals RO1). Deferred, not rejected.

### C. Cross-task meta-pattern library (one shared library across tasks)
- Pros: maximal reuse of model-agnostic patterns (e.g. "set adequate token budget").
- Cons: dilutes task-family specificity; the high-yield *ranking* is task/model-dependent, so a
  shared file would carry low-signal generic advice. Per-task file is the right granularity for
  v1; a thin shared layer can be added later if patterns prove cross-task.

## Consequences

- **Positive:** lowest-blast-radius, highest-yield lever ships first and is measurable in
  isolation (front-load-yield AC); the answer-leak guard is enforceable on a small hand-authored
  file; per-task granularity matches the evidence that membership transfers but ranking does not.
- **Negative:** manual curation cost; the "self-improving" provenance is deferred (acknowledged
  as future work, not a v1 capability).
- The config surface (`CHANGE_LIBRARY_PATH`) is forward-compatible with an auto-mined producer,
  so the deferral costs no rework.
