# Paper Summary — SIA: Self Improving AI with Harness & Weight Updates

> **Reference.** Prannay Hebbar\*, Yogendra Manawat\*, Samuel Verboomen, Alesia Ivanova, Selvam Palanimalai, Kunal Bhatia, Vignesh Baskaran. *SIA: Self Improving AI with Harness & Weight Updates.* arXiv:[2605.27276](https://arxiv.org/abs/2605.27276)v2, 28 May 2026 (Hexo Labs; \*equal contribution). CC-BY-SA 4.0.
>
> This is a condensed, faithful summary of the paper that this repository implements. For the as-built system, see [`architecture.md`](architecture.md); for the original hand-authored architecture notes, see [`architecture_orig.md`](architecture_orig.md).

## 1. Thesis: humans are the bottleneck

Today's AI progress is rate-limited by people — researchers post-train the models, engineers scaffold, prompt, and debug the agents wrapped around them. SIA is one concrete step toward an AI that, *given only a task specification and a verifier*, improves **both** its scaffold **and** its model weights without further human intervention.

The paper frames prior self-improvement research as two disjoint silos:

- **Silo 1 — Harness / scaffold self-improvement.** A meta-agent rewrites the agent's scaffold (system prompt, tool-dispatch logic, retry policy, answer-extraction code) across generations, while the LLM weights stay fixed (Darwin Gödel Machine, Meta-Harness, Hyperagents, AI Scientist). The recurring observation: scaffold edits concentrate on software-engineering hygiene and rarely deliver domain reasoning the base model could not already produce.
- **Silo 2 — Test-time post-training.** A hand-written RL pipeline updates the model's own weights on task feedback, with the harness held fixed at a single prompt-and-grader template (TTRL, Discover/TTT, surprising-effectiveness-of-TTT). The gain comes from internal policy change, but the pipeline is human-engineered and does not adapt to a scaffolded agent's structure.

**The gap SIA closes:** harness work leaves the model fixed; test-time training leaves the harness fixed. SIA is, to the authors' knowledge, the only system that updates **both** the scaffold and the weights inside a single self-improving loop.

## 2. Method: one loop, two levers

SIA is a configurable loop driven by **three LLM components**:

| Component | Symbol | Role | Model used |
|-----------|--------|------|------------|
| **Meta-Agent** | M | Generates the initial scaffold `A₁ = M(U, R)` from the task spec `U` and reference impls `R` | Claude Sonnet 4.6 |
| **Task-Specific Agent** | A_g | The scaffold at generation `g` that actually executes against the evaluation dataset | gpt-oss-120b (+ LoRA) |
| **Feedback-Agent** | F | Reads the previous trajectory and metrics, then synthesises the next improvement | Claude Sonnet 4.6 |

The **scaffold** (equivalently **harness**) is the fixed, non-weight component of the agent: system prompt ∪ tool-dispatch logic ∪ answer-extraction ∪ grader ∪ supporting infrastructure — every part that is fixed code rather than model output.

After each execution, the Feedback-Agent **dynamically selects between two complementary levers** — these are *soft labels*, interleaved freely, not rigid sequential stages:

1. **Harness update** — one scaffold-evolution step; weights `θ` held fixed:
   `A_{g+1} = F(A_g, τ_g(π_θ), E_g, U)` where `τ_g(π_θ)` is the trajectory from running scaffold `A_g` with model `π_θ`.
2. **Weight update** — an RL step on the LoRA adapter `θ`; scaffold held fixed. The Feedback-Agent does **not** run a fixed RL procedure — it picks the training algorithm from observed reward dynamics (see §4).

Each generation `g` follows a three-phase protocol:

1. **Execution** — `A_g` runs on dataset `D` inside a sandbox (read-only dataset directory, read/write working directory). The trajectory `τ_g` — every prompt, model response, tool call, tool result, and extracted answer — is captured.
2. **Analysis** — `F` receives `A_g`'s source, `τ_g`, the metrics `E_g`, and optional sample task descriptions (to discourage single-instance overfitting). Full-trajectory access lets `F` diagnose *specific* failure modes rather than react to summary statistics.
3. **Improvement** — `F` emits an *improvement report* (prose analysis + proposed changes) and the next-generation agent `A_{g+1}`.

![SIA two-lever self-improving loop — Meta-Agent bootstraps the scaffold; the Task-Specific Agent executes against a verifier; the Feedback-Agent selects a harness update (scaffold evolves, weights fixed) or a weight update (LoRA evolves, scaffold fixed) each generation.](diagrams/sia-two-lever-loop/rendered/sia-two-lever-loop.svg)

**Base configuration.** All experiments use `openai/gpt-oss-120b` as the base model; weight updates adapt it via **LoRA (rank r = 32, learning rate 4×10⁻⁵)**. Weight updates execute on H100 GPUs via Modal, the authors' RL training platform (built on verl/HybridFlow, SkyRL, LLaMA-Factory, Axolotl).

## 3. Results across three contrasting domains

SIA is evaluated on three deliberately contrasting tasks — law, systems, biology — chosen because they are commonly used to benchmark other self-improving systems. Two operating points are reported to isolate each lever: **SIA-H** (harness-only best) and **SIA-W+H** (harness + weight updates best).

| Task (metric, ↑ better unless noted) | Initial `A₁` | Prev. SOTA | SIA-H (harness only) | **SIA-W+H** |
|--------------------------------------|--------------|------------|----------------------|-------------|
| **LawBench** — 191-class Chinese criminal charge classification (top-1 acc) | 13.5% | 45.0% | 50.0% | **70.1%** |
| **AlphaEvolve TriMul** — CUDA kernel optimisation on H100 (reward = 1500/runtime) | 0.105 | 1.292 | 0.120 | **1.475** |
| **MAGIC scRNA-seq Denoising** — single-cell RNA imputation (`mse_norm`) | 0.048 | 0.240 | 0.241 | **0.289** |

Headline framing from the abstract (all vs. **prior SOTA**):

- **LawBench: 70.1% vs 45.0%** — a **25.1-point** absolute top-1 gain. Harness updates alone reached 50.0% (a 36.5-pt jump over the 13.5% initial scaffold) by restructuring into a TF-IDF + LinearSVC pipeline; the Feedback-Agent then switched to weight updates (**PPO with GAE**), pushing accuracy to 70.1% (+20.1 pt over harness-only).
- **TriMul CUDA: 1,017 µs vs 1,161 µs prior SOTA** — **12.4% faster** kernels. Harness updates plateaued at 12,483 µs (1.14× speedup); weight updates (**entropic advantage weighting**, which up-weights rare high-reward kernels) drove runtime to 1,017 µs — a **14.02× speedup** over baseline and a **91.9% reduction** from the harness-only peak.
- **Denoising: 0.289 vs 0.240 prior SOTA** — **20.4% better** `mse_norm`. Harness updates stalled at 0.241; the first weight-update checkpoint (**GRPO**) introduced a structural transformation the scaffold never generated — a `np.clip + np.rint` post-processing step rounding imputed counts to non-negative integers, enforcing a biological invariant — lifting `mse_norm` to 0.289 (+20% over harness-only).

**RQ1 — combined vs. harness-only.** SIA-W+H strictly outperforms SIA-H on *every* task. Because each lever occupies a distinct change space (external scaffold vs. internal parameters), neither saturates the gain available from the other.

## 4. What each lever changes (RQ2)

- **Harness iteration → *externalised* changes (RQ2a).** New tools, tighter parsers, search procedures, retry policies, prompt structure. Across the three tasks the Feedback-Agent built increasingly specialised scaffolding (a structured answer-extraction layer + SVC re-ranker on LawBench; a compilation-error parser + timing harness on TriMul; a batched config driver on denoising). The model checkpoint is unchanged — gains come from how the scaffold mediates between model and task environment.
- **Weight updates → *internalised* knowledge (RQ2b).** Domain-specific patterns encoded into parameters that no scaffold edit reaches: sharpened disambiguation of adjacent legal charge categories; H100-specific kernel design patterns (shared-memory tiling, fp32 register accumulation, block-size selection); the biological non-negativity invariant on denoising. This knowledge is task-specific and verifier-aligned, emerging from gradient pressure, not human instruction.

**The Feedback-Agent's training-algorithm menu.** Weight updates are not a fixed procedure — `F` selects from a menu conditioned on observed reward structure:

| Algorithm | Selected when |
|-----------|---------------|
| **PPO + GAE** | Dense step-level rewards; stability is the binding constraint (multi-step / long code-gen) |
| **GRPO** | Cheap rollouts, verifier fires at episode end (classification, short-answer); no value net |
| **Entropic advantage weighting** | Right-skewed rewards; correct solutions rare but high-signal (hard proofs, kernels) |
| **REINFORCE + KL-to-base** | Dense reward, base already near-capable, large parameter moves undesirable |
| **Best-of-N behavioural cloning** | Reward so sparse that policy-gradient signal ≈ 0 (cold-start bootstrap) |
| **DPO** | Verifier ranks outputs but cardinal reward is unreliable (soft quality criteria) |

## 5. Limitations & future work

- **Coupled co-evolutionary Goodhart.** Both levers optimise against the *same* fixed verifier `V`: the harness finds scaffolds easy for the current policy to exploit, and the weights train on data collected through a scaffold that will subsequently change. The joint fixed point is a Nash equilibrium between two optimisers blind to each other's update history — it can look strong on the training verifier while being fragile out-of-distribution.
- **Future — Meta-RL over the action-selection policy.** The Feedback-Agent currently selects levers with a *frozen* LLM prior. A more principled design treats the selection policy itself as the object to learn — an outer MDP over a distribution of tasks — making the improvement mechanism itself self-improving.
- **Future — finer-grained interleaving.** Today the loop alternates harness/weight phases in coarse rounds; a finer schedule (trigger a weight update mid-harness-search, or resume harness search right after a gradient step) could reduce the lag between observing a plateau and acting on it.

## 6. Mapping the paper to this codebase

This repository implements the **harness-update loop (SIA-H)** — the generational scaffold-evolution cycle of §2:

| Paper concept | Where it lives in the code |
|---------------|----------------------------|
| Meta-Agent `M` bootstrapping `A₁` | `sia/orchestrator.py` (meta-agent prompt build + first-generation target-agent synthesis) |
| Task-Specific Agent `A_g` execution in a sandbox | Target agent run as an isolated OS subprocess (RO dataset / RW workdir); see [`architecture.md`](architecture.md) and ADR *target-agent process isolation* |
| Feedback-Agent `F` rewriting the scaffold | `sia/orchestrator.py` (feedback-agent prompt build, analysis → next-generation `target_agent.py` + `improvement.md`) |
| Trajectory `τ_g` + metrics `E_g` | `agent_execution.json` logs + per-task `evaluate.py` → `results.json`, recorded by `sia/context_manager.py` |
| Generation index `g`, `G_max` | The `--max_gen` generation loop; artifacts under `runs/run_{id}/gen_{n}/` |

> **Scope note (verify before relying on it).** The open-source loop here is the **harness lever**. The paper's **weight-update lever (SIA-W)** — LoRA RL on H100 via Modal with the PPO/GRPO/entropic-weighting menu of §4 — is the parametric counterpart and is *not* implemented in `sia/`. Treat SIA-W as the paper's contribution rather than runnable code in this repo unless and until a training stack lands here.

## 7. Glossary

- **Scaffold / harness** — the fixed, non-weight component of an agent (prompt, tools, parsers, grader).
- **Harness update** — scaffold evolves, weights fixed (`SIA-H`).
- **Weight update** — LoRA adapter evolves, scaffold fixed (`SIA-W`); both together is `SIA-W+H`.
- **Generation** — one Execution → Analysis → Improvement cycle.
- **Verifier `V`** — the deterministic, per-instance grader the loop optimises against.
- **`mse_norm`** — normalised reconstruction quality for denoising; ∈ [0, 1], higher is better, 1.0 = perfect imputation.
