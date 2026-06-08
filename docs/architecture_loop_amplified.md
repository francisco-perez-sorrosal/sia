# Harness Loop — Current vs Amplified (Comparison Sketch)

<!-- Additive comparison artifact. Does NOT replace docs/architecture.md or
     docs/architecture_orig.md, both of which remain the canonical descriptions of the
     shipped system. This file sketches a PROPOSED design for side-by-side comparison.
     Source: .ai-work/harness-loop-amplify/SYSTEMS_PLAN.md (design-only, no code shipped). -->

> **Status:** design sketch · proposed, not built. The current loop below is verified against
> `sia/orchestrator.py` and `sia/context_manager.py`; the amplified loop is the proposed design
> from `SYSTEMS_PLAN.md`. Nothing here changes the shipped system.

## Framing

The **current** harness loop is strictly sequential and single-candidate: the Meta-Agent seeds
generation 1 from a per-task reference agent, then each generation runs one target agent, evaluates
it once, and spawns one Feedback Agent that rewrites it for the next generation — which always builds
on the *last* generation, even when that generation regressed. The verifier already computes a rich
per-question failure signal, but the framework drops it (`_extract_metrics:383-385`) and hands `F`
only a raw scalar `results.json`. The **amplified** loop closes those gaps without changing the
loop's shape: it front-loads a per-task change-library into the Meta-Agent's prior (so generation 1
starts strong), feeds `F` a structured failure-taxonomy digest plus a scored trajectory instead of
raw dumps, and bases each next generation on the *best* generation so far while rejecting regressions.
A best-of-N candidate fan-out is held in reserve as a deferred Plan-2 branch, engaged only if the
cheaper levers miss the parity target. The goal is to reach the accuracy that prior runs hit at
generation 4 within **2 working generations** — generically, not just for the SQL proof case.

## Diagram A — Current Loop (sequential, single candidate, base-on-last)

![Current SIA-H loop: Meta-Agent seeds gen-1, target agent runs, verifier produces scalar results.json while per-item detail is dropped at _extract_metrics, Feedback Agent rewrites from the raw results and the last generation, next generation runs sequentially](diagrams/loop-current/rendered/loop-current.svg)

The single feedback edge carries a raw `results.json` and a bare previous-generations integer list
(`build_feedback_prompt:889`, `_run_feedback_agent:753`). The per-item failure detail the verifier
computes is discarded (red node, `_extract_metrics:383-385`). The next generation is built on the
*last* generation's `target_agent.py` (`_run_feedback_agent:750`), so a regression propagates forward.

## Diagram B — Amplified Loop (front-loaded, digest-fed, base-on-best; best-of-N deferred)

![Amplified SIA-H loop: a per-task change-library front-loads the Meta-Agent (Lever C); the verifier emits an optional items array that becomes a failure-taxonomy digest feeding F (Lever B); add_generation retains a scored trajectory feeding F (Lever D); the next generation is selected from the best-so-far with regression rejected (Lever E); a dashed deferred Plan-2 branch adds best-of-N candidate fan-out (Lever A)](diagrams/loop-amplified/rendered/loop-amplified.svg)

Green nodes and the **NEW**-labelled edges are additions over Diagram A; each is annotated with the
lever it implements. The dashed `plan2` subgraph (Lever A, best-of-N) is the deferred branch —
designed but engaged only if the Plan-1 bundle (C + B + E) misses 2-generation parity. Every lever
sits behind an on/off config knob (`sia/config.py`), so reverting all knobs reproduces today's
behavior bit-for-bit.

## Current vs Amplified — Dimension Table

| Dimension | Current | Amplified | Lever |
|-----------|---------|-----------|-------|
| Gen-1 prior | Reference agent only; gen-2→4 discoveries rediscovered each run | Reference agent **+ per-task `change_library.md`** front-loaded into `M` | C |
| Signal to `F` | Raw scalar `results.json`; per-item arrays dropped (`_extract_metrics:383-385`) | **Failure-taxonomy digest** (status/group/category counts + worst-N) from optional `items[]` | B |
| Verifier→feedback contract | Implicit; scalar fields only | **Opt-in, additive `items[]`**; 3 recognized keys; graceful degradation | B |
| Trajectory context | Bare previous-gens integer list (`:753`) | **Scored trajectory** (gen acc → Δ + change descriptor) as a clean table | D |
| Base for next gen | Last generation, even if it regressed (`:750`) | **Best-so-far** (`argmax` accuracy); regression rejected + re-prompted once | E |
| Candidates per gen | Exactly one (sequential) | One by default; **best-of-N (K candidates)** deferred to Plan 2 | A |
| Generation budget to parity | ~4 working generations | Target **2 working generations** | A–E |
| Lever toggling | n/a | Every lever is a config/env knob; OFF reproduces today | A–E |

## Generic and Configurable — SQL Is Only the Proof Case

The amplified design is **task-generic by construction**. Every lever lives in the task-agnostic
`M` / `F` / orchestrator operators; all task knowledge is pushed into per-task data files
(`reference/`, the new per-task `change_library.md`) and per-task grader output — never into
`sia/*.py` branches keyed on a task name. The verifier→feedback contract is **opt-in and
additive**: a grader that emits only scalar `results.json` keeps working unchanged, and every lever
defaults so that turning it OFF reproduces current behavior. SQL (English→SQL) is the **measurement
proof case** for "4→2 compression," not a code path — the same machinery and the same config are
required to demonstrate parity across the other shipped tasks (`lawbench`, plus a second task), each
supplying its own `change_library.md` and `items`-emitting grader.

## See Also

- [`docs/architecture.md`](architecture.md) — canonical developer navigation guide for the shipped system (unchanged).
- [`docs/architecture_orig.md`](architecture_orig.md) — original hand-authored architecture note (unchanged).
- `SYSTEMS_PLAN.md` (pipeline-ephemeral) — full lever mechanics, config schema, phased plans, and acceptance criteria.
