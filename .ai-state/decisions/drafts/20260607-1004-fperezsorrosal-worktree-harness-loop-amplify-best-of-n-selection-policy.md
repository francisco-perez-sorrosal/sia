---
id: dec-draft-a55bd3d7
title: Best-of-N candidate-scaffold selection policy for the harness loop
status: proposed
category: architectural
date: 2026-06-07
summary: Verifier-guided best-of-N per generation selects on a minibatch then confirms the winner on the full held-out set; defaults OFF (K=1).
tags: [harness-loop, best-of-n, verifier, overfit-guard, sia-h]
made_by: agent
agent_type: systems-architect
branch: worktree-harness-loop-amplify
pipeline_tier: standard
affected_files:
  - sia/orchestrator.py
  - sia/context_manager.py
  - sia/config.py
re_affirms: dec-draft-c01d583c
---

## Context

The SIA-H loop is single-candidate per generation: one `F` proposal becomes the next
generation, accepted blind (`orchestrator.py:791-886`). The captured English→SQL runs show
~33% of working generations wasted or harmful — a single proposal that "didn't take" (run_2
g3 plateau) or actively regressed (run_3 g3, −2.08pp). `V` (running the task grader) is cheap,
deterministic, and already per-directory isolated (`run_evaluation(gen_dir, ...)`,
`orchestrator.py:831`), so generating and grading K candidates in parallel is structurally free.
The risk is the paper's coupled-Goodhart limitation (§8): with only a 48-item held-out set,
selecting argmax-accuracy over K candidates can pick a scaffold that games the 48 rather than
the task family.

## Decision

Per generation, generate **K** candidate scaffolds into `gen_N/cand_{0..K-1}/`, grade each,
and promote the best. Selection policy: **rank K on a minibatch** (`BEST_OF_N_MINIBATCH_FRAC`,
default 0.5 of the held-out set), then **confirm the minibatch winner on the full set** before
promotion; a candidate that wins the minibatch but loses the full-set confirmation is rejected
(DSPy minibatch-then-confirm discipline). Tie-break on smaller code size
(`BEST_OF_N_TIEBREAK="smaller_code"`). The feature defaults **OFF** (`BEST_OF_N=1` reproduces
today's single-candidate loop) because it changes the core loop contract and multiplies eval
cost; it is opt-in and engaged only after measurement shows the cheaper front-load lever
(change-library) alone does not reach 2-generation parity.

## Considered Options

### A. Flat best-of-N, select argmax accuracy on the full set (no minibatch)
- Pros: simplest selection logic; one number per candidate.
- Cons: K× full-set eval cost; maximal exposure to 48-item Goodhart (picks the candidate that
  best fits the exact held-out items).

### B. Minibatch-rank + full-set confirm (CHOSEN)
- Pros: bounds cost (K minibatch evals + 1 full confirm); the confirm step is an explicit
  overfit guard — a minibatch overfitter is caught before promotion.
- Cons: more moving parts; requires the grader to honor a sample-subset flag.

### C. Beam / tree search over candidate scaffolds
- Pros: richer search; Scattered-Forest-style code-space exploration.
- Cons: orchestration cost far exceeds a 2-generation budget; overkill (literature: "likely
  overkill for 2 gens").

## Consequences

- **Positive:** removes the regress-then-revert and didn't-take waste modes in one parallel
  round; overfit guard is structural, not advisory; OFF-by-default keeps the change incremental
  and the default behavior bit-for-bit unchanged.
- **Negative:** K× eval cost when enabled (acceptable per the no-runtime-budget constraint);
  highest blast radius of the five levers (touches `run_generation`); requires the
  verifier→feedback contract's minibatch sample-subset support.
- Selection reuses the existing best-by-accuracy logic at `context_manager.py:300-314`,
  factored into a shared `select_best(candidates)` helper.
