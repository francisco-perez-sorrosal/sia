---
id: dec-draft-c01d583c
title: Task-agnostic verifier-to-feedback contract (opt-in items array)
status: proposed
category: architectural
date: 2026-06-07
summary: Graders may optionally emit a top-level "items" array with three framework-recognized keys (status/group/category); absent => graceful fallback to today's behavior.
tags: [harness-loop, verifier-contract, failure-taxonomy, backward-compat, sia-h]
made_by: agent
agent_type: systems-architect
branch: worktree-harness-loop-amplify
pipeline_tier: standard
affected_files:
  - sia/context_manager.py
  - sia/orchestrator.py
  - sia/config.py
re_affirmed_by: [dec-draft-a55bd3d7]
---

## Context

Every shipped grader already computes rich per-question structure — SQL emits per-item
`status`/`db_id`/`hardness`/`gold_sql`/`candidate_sql`; lawbench emits a `per_class` map; chess
emits a `details` array — but the framework flattens all of it to scalars before tracking:
`_extract_metrics` keeps only top-level scalars and explicitly skips lists
(`context_manager.py:383-385`) and nested dicts. So `F` never sees a failure taxonomy; it gets
either a raw `results.json` dump or nothing. A structured failure-taxonomy digest (Lever B) and
scored credit assignment (Lever D) are impossible until this signal survives. The three graders
have three different per-item shapes, so any consumer must either special-case each grader
(violates task-genericity) or agree on one optional canonical shape.

## Decision

Define an **opt-in, additive** contract. A grader **may** emit a top-level array under the
reserved key `"items"`; each element is an open dict in which exactly **three** keys are
framework-recognized, all optional:

- **`status`** — pass/fail-class marker. Anything not in a configurable pass-set
  (`VERIFIER_PASS_STATUSES`, default `{"CORRECT","PASS","correct"}`) counts as a failure.
- **`group`** — any grouping dimension (db_id, class, hardness). Drives "failures concentrated
  in group X" digests. The framework never interprets the value's semantics.
- **`category`** — the grader's own failure-taxonomy label. If absent, the framework derives a
  coarse category from `status`.

All other keys are opaque and ignored by the framework. `_extract_metrics` retains a **bounded**
summary of `items` (per-status / per-group / per-category counts + worst-N ids) instead of
dropping the array. **Graceful degradation:** no `items` key → no digest → `F` falls back to
today's raw-`results.json` behavior; `items` without `category` still yields status/group
digests. No existing grader must change. The SQL grader is the reference implementation; the
other three adopt the contract incrementally.

## Considered Options

### A. SQL-specific failure parsing in the framework
- Pros: fastest path to the demo result.
- Cons: bakes SQL knowledge into `sia/` core — violates the task-generic constraint outright.

### B. Opt-in canonical `items` array, 3 recognized keys (CHOSEN)
- Pros: one generic consumer; backward-compatible (absent key = today's behavior); each grader
  owns its own taxonomy via the opaque `category`; bounded summary prevents context bloat.
- Cons: requires graders to opt in to gain the benefit; coordination to keep shapes consistent.

### C. Mandatory rich schema for all graders
- Pros: uniform signal everywhere.
- Cons: breaking change; forces every grader (incl. third-party tasks) to rewrite; rejected on
  backward-compat grounds.

## Consequences

- **Positive:** Levers B and D become task-generic with a single consumer; zero breakage for
  existing graders; per-task taxonomy lives in the grader (where task knowledge belongs), not in
  core; bounded summary caps context cost.
- **Negative:** benefit is gated on graders opting in; needs light governance so `items` shapes
  stay consistent across tasks (mitigated by only 3 recognized keys + a reference grader).
- Enables the minibatch sample-subset selection that the best-of-N policy
  (`dec-draft-a55bd3d7`) depends on.
