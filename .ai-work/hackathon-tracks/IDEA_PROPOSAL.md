# Idea Proposal: SIA Hackathon — 9 Ideas Across 3 Tracks

**Validation**: Interactive ideation, hackathon mode (spine entry = promethean). Non-interactive single-pass — top picks recommended, not user-selected.

---

## Surface Assumptions (load-bearing)

These shaped every idea below. If any is wrong, the affected ideas shift.

1. **"A day" = ~6–8 focused engineering hours by one person**, with API keys already configured (Anthropic + Google `gemini` keys; the GPQA task already targets `models/gemini-3.1-flash-lite`). Ideas that need >1 day of *core* work are rejected, not reshaped.
2. **A "win" to judges = a number that visibly moves across generations + a narratable loop + an honesty/differentiation angle** (per `EXTERNAL_INSPIRATION.md` §judging). I bias every idea toward producing a generation-over-generation curve.
3. **The task-folder contract is small and well-trodden** — verified against the code. A new task needs exactly:
   - `data/public/task.md` (spec, >50 chars — enforced by `tests/test_task_structure.py`)
   - `reference/reference_target_agent.py` (a working baseline scaffold the meta-agent reads as a pattern)
   - `reference/SAMPLE_TASK_DESCRIPTIONS.md` (sample items)
   - `data/public/evaluate.py` that accepts `--gen-dir` and writes `results.json` (deterministic grader = the verifier `V`)
   - `data/private/<ground-truth>` (held out, read by `evaluate.py` only)
   - The bundled-task path also requires adding the name to `BUNDLED_TASKS` in `orchestrator.py:68`, OR — simpler for a hackathon — just pass `--task_dir ./tasks/my-task` (no code edit needed; `resolve_task_dir` handles external dirs). **This is the single most important feasibility fact: a new Applied task needs ZERO changes to `sia/` if launched via `--task_dir`.**
4. **SIA-W (weight lever) does NOT exist in this repo.** Verified: `orchestrator.py` only runs the harness loop (meta → target → feedback → next scaffold). There is no LoRA/RL/Modal/H100 code. **Every idea below lives entirely in the harness lever (SIA-H).** Any idea implying weight updates is rejected on sight.
5. **The metric the framework already tracks is `accuracy`** (`context_manager.py` `finalize()` keys on `metrics["accuracy"]` and builds the `first% → last% (+gain%)` evolution string). Tasks whose `results.json` emits an `accuracy` field get the cross-generation evolution summary *for free*. Non-accuracy metrics still log but don't get the headline evolution line without a tiny `context_manager` tweak.
6. **The default loop is a linear chain** (`gen_N` → `gen_{N+1}`, keeping only the latest). There is no archive/population. Per `EXTERNAL_INSPIRATION.md`, this is the single biggest framework gap vs. DGM/AlphaEvolve and the most credible Framework-track win.
7. **No sentinel report or idea ledger baseline exists** in `.ai-state/`. Normally promethean halts without a sentinel baseline; here the orchestrator pre-gathered external inspiration and explicitly seated me as the hackathon spine entry, so I proceed and flag the missing baseline rather than blocking. The ideas are grounded in direct code reading instead of a sentinel scorecard.

### Registered objections to the brief

- **Applied-track tasks with slow/non-deterministic verifiers will NOT fit a day.** TriMul CUDA (needs H100) and scRNA-seq (heavy deps, slow eval) from the paper are *out* — they break assumption #1 and #4. I deliberately propose Applied tasks with **fast, deterministic, cheap verifiers** (string/exact-match or sklearn-metric graders) so 3+ generations fit in an afternoon. This is a feature, not a compromise: a cheap verifier is exactly what makes the loop demoable.
- **"Wire SIA onto a real-world problem" can balloon.** I scope each Applied idea to a dataset that already exists in a parseable form and a grader that is <60 lines of Python, mirroring the shipped `gpqa/evaluate.py`.

---

## Internal SIA findings (what the code actually supports)

| Capability | Status in `sia/` | Consequence for ideas |
|---|---|---|
| Harness loop (meta → target → feedback) | **Built** (`orchestrator.py`) | All ideas exploit this |
| Weight lever (LoRA/RL) | **NOT built** | No idea may depend on it |
| New task via `--task_dir` (no code edit) | **Built** (`resolve_task_dir`) | Applied tasks are ~0-risk to `sia/` |
| Deterministic verifier `evaluate.py` → `results.json` | **Built** (`run_evaluation`, 600s timeout) | Verifier-centric ideas are cheap |
| Cross-generation evolution summary on `accuracy` | **Built** (`context_manager.finalize`) | Free "number moves" demo if metric is `accuracy` |
| LLM-written per-gen change summary | **Built** (`_generate_llm_summary`) | The narratable "diff" is already produced |
| Multi-trajectory (per-item) vs single-trajectory logging | **Built** (auto-detected) | Per-item tasks (QA) and single-artifact tasks (ML) both supported |
| Docker sandbox (`--network none`, RO data, mem/cpu caps) | **Built** (`_run_target_agent_sandboxed`) | Reward-hacking / safety ideas have a real boundary to instrument |
| Population / archive / parent-selection | **NOT built** | The headline Framework gap |
| Held-out / second verifier | **NOT built** (single `evaluate.py`) | The headline Research gap (Goodhart) |
| Backend choice (`claude` / `openhands`) | **Built** | Cross-model comparison is config-only |

**Divergence flagged:** the paper's headline tasks are LawBench / TriMul / scRNA-seq, but the *shipped* tasks are `gpqa`, `lawbench`, `longcot-chess`, `spaceship-titanic`. LawBench *is* shipped (good — a known-good accuracy task to anchor demos). TriMul/scRNA-seq are paper-only and infeasible in a day. Build on the shipped four.

---

# Applied AI Track — use SIA on a real domain task

## A1. "Regex-Golf SIA" — self-improving text-extraction agent on a held-out corpus *(safe / polished)*

- **Pitch:** Point SIA at a structured-information-extraction task (e.g., pull `(drug, dose, route)` triples from clinical-style sentences, or `(amount, vendor, date)` from receipt text) and watch the Feedback-Agent evolve the parser/scaffold from naive prompting to a hardened extract-and-validate pipeline.
- **Track / components:** Applied. New task via `--task_dir` (zero `sia/` edits). Mirrors `gpqa` folder shape; `evaluate.py` does exact-match / F1 over fields. Touches: nothing in `sia/` — pure task authoring.
- **What you build in a day:** ~80 sentences with gold triples in `data/private/`; a `task.md`; a deliberately weak `reference_target_agent.py` (single prompt, no validation); a `<60-line` `evaluate.py` computing field-level F1 and writing `results.json` with an `accuracy` key (so the evolution summary fires).
- **How to win:** The Feedback-Agent's improvement.md will narrate a believable engineering arc — "added a JSON-schema validator, then a retry-on-malformed step, then a normalizer for units." Show the `context.md` evolution line `41% → 78% (+37%)` plus the *diff* of what F invented. Hits judging angles #1 (visible loop) and #3 (number moves).
- **Feasibility (1-day):** High. Riskiest part is gold-label quality on 80 sentences — cut to 40 if time-short. F1 grader is the only non-trivial code and it's <60 lines.
- **Demo:** `sia --task_dir ./tasks/extract --max_gen 4`; show the rising F1 curve from `context.md` + the gen-2 improvement.md where F added validation.
- **Dependencies/risks:** Anthropic/Gemini key only. No GPU. Gold-label authoring is the only labor.

## A2. "SIA-for-SQL" — agent that self-improves on natural-language-to-SQL with an execution verifier *(ambitious / high-ceiling)*

- **Pitch:** Give SIA a tiny SQLite DB and NL→SQL questions; the verifier *executes* the generated SQL and compares result sets to gold. The Feedback-Agent evolves the scaffold (schema-injection, few-shot exemplars, self-repair on SQL errors) generation over generation.
- **Track / components:** Applied. New task via `--task_dir`. `evaluate.py` runs candidate SQL against a fixture DB (deterministic, execution-based grading = a genuinely robust verifier). Touches: nothing in `sia/`.
- **What you build in a day:** A 6-table SQLite fixture (or reuse a public one like the `chinook` DB); ~40 NL questions + gold SQL; `evaluate.py` that executes both and compares sorted result sets → exact-match accuracy. Weak reference agent = "just ask the model for SQL, run it."
- **How to win:** Execution-based verification is the gold standard the reward-hacking literature respects (`EXTERNAL_INSPIRATION.md` §reward-hacking). The "wow" is catching the Feedback-Agent *inventing self-repair* — it sees a SQL error in the trajectory and adds an error-feedback retry loop. That is SIA's analog of DGM's "discovered peer-review" moment (judging angle #2).
- **Feasibility (1-day):** Medium-high. Riskiest: getting the sandbox to bundle the SQLite DB into the RO `data/` mount (it's just a file — low risk). If `--sandbox docker` fights you, run `--sandbox none` (the default) for the demo. Cut to 25 questions if slow.
- **Demo:** Execution-accuracy `30% → 65%` across 4 gens; live-read the gen-3 scaffold's new `try/except → re-prompt with error` block.
- **Dependencies/risks:** API key only. SQLite is stdlib. No GPU. Watch eval timeout (600s default) if questions are many — keep the set small.

## A3. "SIA Triages Itself" — self-improving log-anomaly classifier over the framework's own run logs *(clever / differentiated)*

- **Pitch:** A meta-flavored Applied task: SIA improves an agent that classifies *agent-execution failures* (timeout / malformed-JSON / wrong-format / crash) from `target_agent_stdout.log` + `agent_execution.json` snippets — exactly the failure taxonomy SIA itself produces.
- **Track / components:** Applied, with a reflexive twist. New task via `--task_dir`. Training data is *generated for free* by running existing bundled tasks a few times and hand-labeling the failure type. `evaluate.py` = multiclass accuracy. Touches: nothing in `sia/`.
- **How to win:** The narrative writes itself — "we used SIA to build a tool that makes SIA more debuggable." Differentiated framing judges remember (judging angle #2: surprising/self-referential). The evolved classifier is genuinely useful downstream (feeds a future dashboard).
- **What you build in a day:** Run 2–3 bundled tasks, harvest ~60 log excerpts, label into 4 classes; `task.md`; weak reference agent (keyword-match baseline); accuracy `evaluate.py`.
- **Feasibility (1-day):** High. Riskiest: harvesting enough *failure* examples (success logs are easy; you may need to deliberately break a task to get failures). Cut to 3 classes if needed.
- **Demo:** Confusion-matrix improving across gens; accuracy `55% → 80%`. Bonus: show the evolved classifier run live on a fresh failing log.
- **Dependencies/risks:** API key only. The only labor is labeling; no external dataset dependency at all (data is self-sourced).

---

# Framework Enhancement Track — improve SIA itself

## F1. "Archive Mode" — keep a population of scaffolds + best-parent selection *(ambitious / high-ceiling)*

- **Pitch:** Replace the linear `gen_N → gen_{N+1}` chain with a **growing archive**: every generation, the Feedback-Agent improves *the best-scoring scaffold so far*, not merely the last one. This is the DGM/AlphaEvolve move (`EXTERNAL_INSPIRATION.md` §takeaway) — the single biggest stated gap.
- **Track / components:** Framework. Touches `orchestrator.py` (`run_generation`, the `for current_gen` loop, `_run_feedback_agent`) and lightly `context_manager.py` (track best gen — `finalize()` already computes `best_gen` by accuracy, so the selection logic is half-written).
- **What you build in a day:** A `--select-parent {last,best}` flag. When `best`, before building the feedback prompt, read all prior `results.json`, pick the max-accuracy gen, and feed *that* gen's `target_agent.py` to F as the agent-to-improve (instead of `gen_{current}`). The archive is just the existing `runs/run_X/gen_*/` dirs — no new storage.
- **How to win:** A/B the SAME task under `--select-parent last` vs `best` and show best-parent escapes a local-optimum dip that the linear chain gets stuck in. Two curves on one chart = an instantly legible "we improved the algorithm" story. Directly answers the field's stated frontier.
- **Feasibility (1-day):** Medium. Riskiest: the feedback prompt and `_run_feedback_agent` assume "improve gen_{current-1}"; you must thread the chosen-parent path through `build_feedback_prompt` + `_build_feedback_context`. ~3 functions, disjoint, surgical. If time-short, hardcode `best` instead of a flag.
- **Demo:** Side-by-side evolution curves (linear vs archive) on `gpqa` or the A1 task; best-parent ends higher.
- **Dependencies/risks:** API key only. Risk is purely orchestrator plumbing; no new deps. Keep `--max_gen` ≤4 so both runs fit.

## F2. "Cost-Aware Loop" — token/cost budget + per-generation cost-per-point reporting *(safe / polished)*

- **Pitch:** SIA currently optimizes accuracy blind to spend. Add a cost ledger: capture token usage per generation and report **cost-per-accuracy-point** in `context.md`, plus an optional `--cost-budget` early-stop.
- **Track / components:** Framework. Touches `context_manager.py` (`_extract_metrics` already pulls `total_cost_usd`/`total_*_tokens` from `results.json` generically — they're in the GPQA `task.md` schema!) and `finalize()` (add a cost row). Optionally `orchestrator.py` for the budget stop.
- **What you build in a day:** Extend `finalize()` to compute `Δaccuracy / Δcost` across generations and emit a "marginal cost per point" line; add a `cost_summary` block. The token fields already flow through `_extract_metrics` — you're mostly formatting existing data. `--cost-budget` stops the loop when cumulative cost exceeds a cap.
- **How to win:** The Holistic Agent Leaderboard paper (`EXTERNAL_INSPIRATION.md`) explicitly calls cost-aware reporting "missing infrastructure." Demoing "gen 3 cost 4× more for +2% — diminishing returns, auto-stopped" is exactly the honesty instrumentation judges reward (judging angle #4).
- **Feasibility (1-day):** High. Riskiest: target agents must actually emit token counts into `results.json` (GPQA's schema does; others may not). Cut the budget-stop and keep just the reporting if short.
- **Demo:** A `context.md` cost table showing rising cost-per-point + a run that auto-stops at budget.
- **Dependencies/risks:** API key only. Depends on tasks reporting tokens — anchor the demo on `gpqa` whose schema guarantees them.

## F3. "Parallel Generation Fan-out" — N candidate scaffolds per generation, keep the best *(clever / differentiated)*

- **Pitch:** Each generation, run the Feedback-Agent *K times* (different seeds/temperature) to produce K candidate `target_agent.py`, evaluate all K, and carry the best into the next generation. Beam-search over scaffolds instead of greedy single-step.
- **Track / components:** Framework. Touches `orchestrator.py` (`run_generation`, `_run_feedback_agent`, the target-agent run+eval block). Reuses `run_evaluation` per candidate.
- **What you build in a day:** A `--beam-width K` flag. In `run_generation`, after the feedback step, loop K times into `gen_N/cand_0..cand_{K-1}/`, run+evaluate each, pick max-accuracy as the canonical `gen_N`. The eval/run helpers already take a gen-dir, so you parameterize the path.
- **How to win:** Variance reduction is visible and intuitive — show that beam-width-3 reaches a target accuracy in fewer *generations* than greedy, trading parallel compute for fewer serial rounds. Pairs beautifully with F1 (archive) as a combined "we made the search smarter" story.
- **Feasibility (1-day):** Medium. Riskiest: K parallel target-agent runs multiply wall-clock and cost — keep K=2–3 and `--max_gen` small, or run candidates sequentially (simpler, still demoable). Sandbox dirs must not collide (use `cand_i/` subdirs).
- **Demo:** "Greedy needs 5 gens to hit 70%; beam-3 hits it in 3." Two curves, x-axis = generation.
- **Dependencies/risks:** API key only. Cost is the real constraint — bound K and gens. No GPU.

---

# Research Track — evals, methodology, self-improvement experiments

## R1. "Held-Out Verifier" — measure harness-update reward-hacking on a second grader *(ambitious / high-ceiling)*

- **Pitch:** SIA's own paper §5 names "coupled co-evolutionary Goodhart" as its top limitation. **Operationalize it:** add a *second, held-out* verifier and report, per generation, the gap between training-verifier accuracy and held-out accuracy. A widening gap = measured reward-hacking.
- **Track / components:** Research. Touches `orchestrator.py` (`run_evaluation` → run a second `evaluate_heldout.py`) and `context_manager.py` (log both metrics + their gap). Task-side: a held-out split of `data/private/`.
- **What you build in a day:** Split any accuracy task's private set into `train_verifier` / `heldout_verifier`; add a second eval invocation writing `results_heldout.json`; extend `_extract_metrics` to capture both; plot `train_acc` vs `heldout_acc` per generation.
- **How to win:** This is the *gift-wrapped* research win (`EXTERNAL_INSPIRATION.md` §direct tie-in) — SIA is the perfect substrate to *measure* the very Goodhart gap its authors flagged. If you catch a generation where train-acc rises while held-out-acc stalls/drops, that is a publishable-flavored result in a single demo (judging angle #4, honesty instrumentation = differentiation).
- **Feasibility (1-day):** Medium. Riskiest: you need enough held-out data for a stable estimate — use LawBench or GPQA which have ample items; split 70/30. The second-eval plumbing is a near-copy of the existing `run_evaluation`.
- **Demo:** One chart, two lines (train vs held-out) across gens; annotate the divergence point.
- **Dependencies/risks:** API key only. Risk = noise on small held-out sets; anchor on the largest shipped task. No GPU.

## R2. "Improvement Taxonomy" — auto-classify what the Feedback-Agent actually changes each generation *(safe / polished)*

- **Pitch:** The paper claims harness edits cluster into categories (new tools, tighter parsers, retry policies, prompt restructuring — RQ2a). **Test it empirically:** classify every `improvement.md` across a run into a taxonomy and chart the distribution — does SIA, like prior work, over-index on SE-hygiene vs. domain reasoning?
- **Track / components:** Research / methodology. Read-only over `runs/*/gen_*/improvement.md` (the Feedback-Agent already writes these). A small analysis script (can live in `scripts/`, zero `sia/` edits) + an LLM-or-keyword classifier into ~6 categories.
- **What you build in a day:** A standalone `analyze_improvements.py` that walks a run, classifies each improvement.md (LLM call or keyword rules) into {new-tool, parser-hardening, retry/robustness, prompt-restructure, validation, task-specific-hack}, and emits a stacked-bar chart + a "% SE-hygiene vs % domain-reasoning" headline.
- **How to win:** Replicates and visualizes a *named claim from the paper* on the shipped code — strong "we validated the science" framing. The "task-specific-hack" bucket doubles as a cheap reward-hacking smell test. Low-risk, high-legibility.
- **Feasibility (1-day):** High. Pure post-hoc analysis — no orchestrator changes, no sandbox, no flaky runs. Riskiest: needs a multi-generation run to analyze; reuse any existing `runs/` output or do one cheap GPQA run first.
- **Demo:** A stacked bar over generations + the headline ratio; compare against the paper's qualitative claim.
- **Dependencies/risks:** API key (only if using LLM classifier; keyword fallback needs none). No GPU. Depends on having ≥1 multi-gen run.

## R3. "Generation-Efficiency Curve" — does SIA improve monotonically, and where does it plateau? *(clever / differentiated)*

- **Pitch:** Systematically characterize the *shape* of self-improvement: run SIA to high `--max_gen` on 2–3 shipped tasks and quantify monotonicity, plateau generation, and regression frequency (gens that get *worse*). Produces a reusable "SIA scaling law (harness-only)" mini-report.
- **Track / components:** Research / methodology. Mostly orchestration of existing runs + analysis over `context.md` / `results.json`. Optional tiny `context_manager` patch to flag regressions. No core `sia/` behavior change required.
- **What you build in a day:** A driver that runs each task to `--max_gen 6–8`, then an analysis script computing per-task: peak gen, % of gens that regressed, marginal gain per gen. Cross-task plot.
- **How to win:** Answers a question the paper leaves implicit ("coarse rounds", future-work §5) with data — "harness-only SIA plateaus around gen N and regresses X% of the time." Differentiated because it's *measurement of the method*, not another score. Feeds directly into F1 (archive mode fixes regressions) — a natural narrative pairing.
- **Feasibility (1-day):** Medium. Riskiest: running multiple tasks to gen-8 is the wall-clock/cost sink — use the *cheapest* tasks (smallest item counts), cap models to `haiku`/`flash-lite`, and parallelize runs across terminals. If time-short, report 1 task deeply instead of 3 shallowly.
- **Demo:** Multi-task efficiency curves with annotated plateau + regression markers.
- **Dependencies/risks:** API key + budget (this idea spends the most on inference). No GPU. Cost-control is the main risk — keep item counts and models small.

---

## Comparison Table

| # | Idea | Track | 1-day confidence | Win ceiling | Key risk |
|---|------|-------|:---:|:---:|------|
| A1 | Regex-Golf SIA (info extraction) | Applied | High | Medium | Gold-label quality |
| A2 | SIA-for-SQL (execution verifier) | Applied | Med-High | High | Sandbox DB mount / question count |
| A3 | SIA Triages Itself (log classifier) | Applied | High | Medium | Sourcing enough failure logs |
| F1 | Archive Mode (best-parent select) | Framework | Medium | High | Orchestrator plumbing across 3 fns |
| F2 | Cost-Aware Loop (cost/point report) | Framework | High | Medium | Tasks must emit token counts |
| F3 | Parallel Fan-out (beam over scaffolds) | Framework | Medium | Med-High | K× cost/wall-clock; dir collisions |
| R1 | Held-Out Verifier (Goodhart gap) | Research | Medium | High | Held-out set size / noise |
| R2 | Improvement Taxonomy (RQ2a replication) | Research | High | Medium | Needs ≥1 multi-gen run to analyze |
| R3 | Generation-Efficiency Curve (plateau) | Research | Medium | Med-High | Inference cost of many long runs |

## Top pick per track

- **Applied → A2 (SIA-for-SQL).** Execution-based verification is the most defensible "real number" a judge can trust, and catching the Feedback-Agent inventing SQL self-repair is the highest-ceiling "wow" in the track for the lowest dataset-authoring cost.
- **Framework → F1 (Archive Mode).** It closes the single biggest, explicitly-named gap (linear chain vs. population), the selection logic is already half-written in `finalize()`, and the A/B-curve demo is the most legible "we improved the algorithm" story available in a day.
- **Research → R1 (Held-Out Verifier).** It operationalizes SIA's own §5 limitation into a measurable, gift-wrapped result; a single divergence chart is both a strong demo and the clearest differentiation from naive "score went up" submissions.

> **Combo note:** F1 + R1 + R3 chain into one coherent narrative — *measure* that linear SIA plateaus and reward-hacks (R3, R1), then *fix* it with archive selection (F1). If a team wants one ambitious thread instead of three disjoint demos, that's the strongest spine.

---

## Visual & Dynamic Web-Demo Potential

All 9 ideas emit the same shape of artifact — a **per-generation series**: the `context.md` evolution line, the `improvement.md` diffs, the `results.json` metrics, and the evolving `target_agent.py`. SIA's loop is inherently *temporal* (generations), so the unifying web primitive is a **generation scrubber** — a slider/timeline that, as you drag across generations, simultaneously moves a number, redraws a chart, and diffs the code. A web page here is mostly a *thin reader* over files SIA already writes, so build cost is low and payoff is high.

Ideas are ranked below by how much a dynamic web page *is* the win (not just polish).

### Tier 1 — the visual is the differentiator

**R1 — Held-Out Verifier** (strongest web demo in the set). The entire result *is* a divergence between two lines (train-verifier vs held-out accuracy). CLI can't sell "the gap widens"; a chart can.
- **Viz:** dual-line chart, x = generation, with the area between the lines shaded as the "Goodhart gap."
- **Dynamic:** scrub the generation slider; when train-acc rises while held-out stalls, auto-drop a red annotation ("reward-hacking detected at gen N"). A live-growing gap is a visceral "aha."
- **Feeds from:** `results.json` + `results_heldout.json` per gen.

**F1 — Archive Mode** (best A/B story). Two full trajectories (`--select-parent last` vs `best`) on one chart.
- **Viz:** two evolution curves overlaid; the linear chain visibly dips into a local optimum, best-parent escapes upward.
- **Dynamic:** a toggle to play each run's generations in sync, and a "branch pointer" showing *which* prior gen best-parent selected each round (linear always points at N-1; archive jumps back to the peak). That selection-arrow animation is the algorithmic improvement made legible.
- **Feeds from:** two runs' `runs/run_X/gen_*/results.json`.

**F3 — Parallel Fan-out** (the only genuinely graph-shaped idea). Beam search over scaffolds is a tree — hard to convey in text, natural on a canvas.
- **Viz:** a branching tree, K candidate nodes per generation, winner highlighted and carried forward, losers greyed/pruned.
- **Dynamic:** animate generation-by-generation expansion; click any candidate node to see its `target_agent.py` and its score. Overlay "beam-3 hits 70% in 3 gens vs greedy's 5" as a second mini-chart.
- **Feeds from:** `gen_N/cand_*/results.json`.

### Tier 2 — chart + live code diff (strong polish)

**A2 — SIA-for-SQL** (and **A1** by the same mechanic). The compelling pair is *the number climbing alongside the code that produced it*.
- **Viz:** split pane — rising execution-accuracy curve on the left, a Monaco/diff viewer on the right showing `target_agent.py` gen-over-gen.
- **Dynamic:** scrub generations; the diff highlights the moment the Feedback-Agent *invents self-repair* (the new `try/except → re-prompt with SQL error` block lights up green) exactly as accuracy jumps. That synchronized "code change → metric jump" is the "wow." A1 is identical with an F1 curve and a JSON-validator block appearing.
- **Feeds from:** `improvement.md` (the narrated diff is already generated) + `context.md`.

**R3 — Generation-Efficiency Curve.** Multi-task plateau/regression characterization.
- **Viz:** overlaid per-task curves with plateau markers and red dots on regressing generations.
- **Dynamic:** toggle tasks on/off; hover a regression marker to read that gen's `improvement.md` ("what change made it worse"). Pairs narratively with F1 (regressions are what archive mode fixes).
- **Feeds from:** `context.md` / `results.json` across long runs.

### Tier 3 — one chart suffices (web is nice, not transformative)

- **A3 — Log classifier:** an animated confusion-matrix heatmap that sharpens across generations reads well, but it's a single component, not an interactive app.
- **R2 — Improvement Taxonomy:** a stacked-bar distribution over generations + the "% SE-hygiene vs % domain-reasoning" headline. Mostly a static chart; mild hover interactivity.
- **F2 — Cost-Aware Loop:** inherently tabular (cost-per-point ledger). A cost-vs-accuracy line with a diminishing-returns annotation is the only dynamic bit; least suited to a rich web page.

### Web-demo summary table

| # | Idea | Web-demo tier | Core visualization | Dynamic element |
|---|------|:---:|------|------|
| R1 | Held-Out Verifier | **1** | Dual-line divergence + shaded Goodhart gap | Scrub gens → gap widens, auto reward-hacking annotation |
| F1 | Archive Mode | **1** | Two overlaid evolution curves (linear vs best-parent) | Sync-play + animated parent-selection branch pointer |
| F3 | Parallel Fan-out | **1** | Branching beam-search tree | Animated expansion; click candidate → code + score |
| A2 | SIA-for-SQL | 2 | Accuracy curve ∥ `target_agent.py` diff viewer | Scrub gens → self-repair block lights up as score jumps |
| A1 | Regex-Golf SIA | 2 | F1 curve ∥ scaffold diff viewer | Scrub gens → validator block appears as F1 climbs |
| R3 | Efficiency Curve | 2 | Multi-task curves + plateau/regression markers | Toggle tasks; hover regression → `improvement.md` |
| A3 | Log classifier | 3 | Animated confusion-matrix heatmap | Heatmap sharpens across gens |
| R2 | Improvement Taxonomy | 3 | Stacked-bar distribution over gens | Hover bar segments |
| F2 | Cost-Aware Loop | 3 | Cost-per-point ledger + cost/accuracy line | Diminishing-returns annotation |

### Recommended single web artifact

Build the **generation-scrubber shell once** (reads `runs/<run>/gen_*/`), then R1 and F1 render inside it nearly free (both are dual-series charts), and A2's code-diff pane reuses the same scrubber. That combo — **F1 + R1 with the A2 diff viewer** — is also the "measure the pathology, then fix it" narrative spine from the combo note above, and it's the set that *most* benefits from being a live web page rather than CLI output.
