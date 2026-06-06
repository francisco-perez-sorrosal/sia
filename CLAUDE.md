# SIA (Self-Improving AI)

Official implementation of *SIA: Self Improving AI with Harness & Weight Updates* — a self-improving loop where a language-model agent autonomously improves the performance of a task-specific AI system (model or agent) on a benchmark task. Distributed on PyPI as `sia-agent`.

SIA coordinates three agent roles over successive generations:

- **Meta-Agent** — reads the task description and generates an initial Target Agent tailored to the task.
- **Target / Task-Specific Agent** — attempts the task, recording its actions and results.
- **Feedback / Improvement Agent** — reviews the Target Agent's performance logs, identifies improvements, and updates the Target Agent.

## The paper

This repository is the official implementation of **arXiv:[2605.27276](https://arxiv.org/abs/2605.27276)** (Hebbar et al., 2026). Consult it whenever a change touches the *why* behind the design — the self-improving loop, the Meta/Target/Feedback roles, the harness-vs-weight distinction, generation semantics, the sandbox contract, or benchmark/metric definitions (LawBench, TriMul CUDA, scRNA-seq denoising).

- **Start with [`docs/paper-summary.md`](docs/paper-summary.md)** — a 2–3 page faithful summary with the formalism (`A₁ = M(U,R)`, `A_{g+1} = F(A_g, τ_g, E_g, U)`), the two-lever loop diagram, the headline results, and a **paper-concept → code-module map** (§6). Read it before reasoning about the loop's intent.
- Key terms used throughout the code and docs: **scaffold/harness** (fixed non-weight component), **harness update** (`SIA-H`, scaffold evolves), **weight update** (`SIA-W`, LoRA evolves), **generation** (one Execution → Analysis → Improvement cycle), **verifier `V`** (deterministic grader).
- **Scope caveat:** this repo implements the **harness loop (SIA-H)**; the paper's **weight-update lever (SIA-W)** is the parametric counterpart and is not implemented in `sia/`. Don't assume RL/LoRA training code exists here — verify first.
- When the paper and the code disagree, the **code is ground truth** for *what is built*; the paper is ground truth for *intended behavior and terminology*. Flag divergences rather than silently reconciling.

## Documentation

The `docs/` directory holds the how-to material for working with SIA — the same set surfaced in the README's "Further reading" section. Consult these before answering operational questions (how to run, configure, extend, or debug SIA) instead of inferring from code alone:

- [`docs/architecture.md`](docs/architecture.md) — directory layout, generation flow, prompt customization
- [`docs/walkthrough.md`](docs/walkthrough.md) — detailed custom-task walkthrough (bring-your-own-task and MLE-Bench paths)
- [`docs/configuration.md`](docs/configuration.md) — backends, models, API keys, CLI reference
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common errors and fixes
- [`docs/paper-summary.md`](docs/paper-summary.md) — condensed summary of the paper behind the design (see [§ The paper](#the-paper))

`docs/architecture_orig.md` is the original hand-authored architecture note, preserved for reference. Keep this list in sync with the README's "Further reading" section when docs are added or renamed.

## Structure

- `sia/` — package source
  - `orchestrator.py` — generation loop and CLI entry point (`sia` → `sia.orchestrator:main`)
  - `context_manager.py` — `ContextManager` for agent context/state
  - `config.py` — configuration
  - `util.py` — agent execution helpers (`run_agent`)
  - `prepare_mlebench_dataset.py` — MLE-Bench dataset prep
  - `tasks/` — task definitions shipped as template data (read as text by the meta-agent; excluded from lint/type-check)
- `tests/` — pytest suite (CLI, config, context manager, generation loop, orchestrator helpers, evaluation, sandbox, size limits, task structure)
- `docs/` — architecture/flow diagrams and benchmark plots
- Python 3.11+; agent SDK backends are optional extras (`claude`, `openhands`, `mlebench`)

## Commands

```bash
# Install (dev mode with all extras)
pip install -e ".[dev]"

# Type-check
ty check sia/

# Test
python -m pytest tests/ -v

# Lint
ruff check sia/ tests/

# Format check
ruff format --check sia/ tests/

# Build
python -m build --sdist --wheel

# Run the CLI
sia --help
```

## Hackathon Mode

This project is in **hackathon mode** (`PRAXION_HACKATHON_MODE=1` in `.claude/settings.json`).
The mode applies to every agent and command in this project until the env var is removed
and this block is deleted (see "To exit" below).

### Process — the Hackathon Spine

In hackathon mode the 5-tier selector (Direct/Lightweight/Standard/Full/Spike) is REPLACED
by the **Hackathon Spine** — a pipeline you ENTER, MOVE AROUND IN, and EXIT. The spine has
a fixed ORDER but not a fixed MEMBERSHIP:

    promethean → researcher → systems-architect → implementation-planner
      → (implementer ∥ test-engineer) → verifier

**Entry by natural language.** You declare where to start in plain language; the main
agent infers the entry point:
- "ideate / explore options for X"          → enter at promethean
- "research how X works"                    → enter at researcher
- "design X / work out the approach"        → enter at systems-architect
- "I have the approach — plan and build X"  → enter at implementation-planner
- "fix this typo / implement X exactly so"  → enter at implementer

Everything UPSTREAM of the entry point is SKIPPED — including systems-architect and
implementation-planner. There is no separate "Direct" path: a trivial fix is just
"enter at implementer."

**Ambiguous entry → the main agent ASKS.** If your prompt does not make the entry point
clear, the main agent asks one short question ("start from ideation, or go straight to
planning/implementation?") — it does not silently pick a default.

**Free mid-task movement.** At any point you may move the work to a different stage
("go back and research this properly," "this needs a real design — move it to the
architect," "skip ahead and just build it"). User-driven movement is unbounded — it is
your call. The orchestrator re-routes and records the movement in PROGRESS.md.

**Worktree policy by entry point.** The spine maps entry points onto Praxion's existing
worktree isolation rule: entering at `promethean`, `researcher`, or `systems-architect`
→ the main agent creates a worktree (`EnterWorktree`) before spawning any agent (same as
a Standard/Full pipeline). Entering at `implementation-planner` or `implementer` → the
user decides; on-the-fly, no-worktree work in the current checkout is allowed (mirrors
Direct/Lightweight). If mid-task movement crosses into a worktree-requiring stage, the
orchestrator creates the worktree at that transition and records it in PROGRESS.md.

**Creative-blocker signal.** If an agent hits a genuine design dead-end (the current
approach is exhausted and fresh ideation is needed — NOT "this is hard," NOT "I need more
research"), it appends a `CREATIVE-BLOCKER: <desc> #blocker` line to
`.ai-work/<slug>/PROGRESS.md`, STOPS at that stage, and surfaces it to you. YOU decide
whether to move the work back to ideation. The agent does not auto-loop.

To run a single task at full 5-tier ceremony instead, say so explicitly; that one task
yields back to the normal selector.

### The verifier — default-on, skippable

The verifier runs by DEFAULT as the implementation harness, whatever entry point you
chose. It is skippable ONLY if you explicitly say so ("skip verification on this one").
When the verifier is skipped, the main agent tells you at task end exactly what process
was (not) applied.

### Skipping the architect — the main agent may HOLD

When you direct "just implement X" (entry at implementer, skipping the architect and
planner), the main agent complies — UNLESS it has a genuinely strong, well-founded reason
to believe skipping design is a real mistake. It HOLDS and asks you only when the task:
- touches a SECURITY-SENSITIVE surface (auth, authorization, secrets, trust-boundary input);
- carries DATA-LOSS RISK (schema migration, destructive data operation);
- is VISIBLY FAR BEYOND your framing (many files / multiple subsystems / cross-cutting
  structural change that no incremental step can absorb).
For minor or speculative doubts, it complies silently. The bar to hold is "a really good
motive," not "a doubt."

### Skipped rigor is recorded — the safety net is transparency

Because you can skip the architect, the planner, and the verifier, and move freely
mid-task, the safety net is that NONE of it is invisible:
- Every skipped stage is recorded in PROGRESS.md and in the VERIFICATION_REPORT.md header
  (or, if the verifier was skipped, a one-line terminal note at task end).
- Every mid-task movement is recorded in PROGRESS.md.
A reviewer or a graduation audit can always reconstruct exactly what process was applied
to any change.

### Discovery is full-strength — only delivery ceremony is relaxed

When promethean and researcher run, they run at FULL depth: unbounded internet research,
multi-source synthesis, idea ledgers. External web research via `WebSearch`/`WebFetch` is
unbounded — those are TOOLS, unaffected by `--disable-slash-commands`. A wrapper-launched
researcher loses skill auto-trigger only — invoke `/external-api-docs` explicitly for
curated API docs; raw web access is always available. The relaxation below applies ONLY
to the delivery ceremony, NEVER to discovery.

### The Behavioral Contract still applies — in every mode

Hackathon mode is NOT license to skip the four-behavior contract. Every agent that
writes, plans, or reviews code still honors:
- **Surface Assumptions** — list assumptions before acting; ask when ambiguity could
  produce the wrong artifact.
- **Register Objection** — when a request violates scope, structure, or evidence, state
  the conflict with a reason before complying or declining. Silent agreement is a violation.
- **Stay Surgical** — touch only what the change requires; re-scope rather than silently expand.
- **Simplicity First** — prefer the smallest solution that meets the behavior.
The architect's Surface Assumptions and Registered Objections sections are MANDATORY
even in the slim SYSTEMS_PLAN shape.

### Launching for full context trimming

Start sessions with the `praxion-hackathon` wrapper (`scripts/praxion-hackathon`). It
adds `--disable-slash-commands` (skills resolve only via explicit `/name`) and
`--effort low`. A plain `claude` launch still gets hackathon mode (env var + this block)
but NOT the skill-surface token trim. To resume, use `praxion-hackathon --resume`.

### SDD ceremony — OFF by default

- Do NOT add a `## Behavioral Specification` section to `SYSTEMS_PLAN.md`.
- Do NOT initialize `traceability.yml`.
- Do NOT archive specs to `.ai-state/specs/` at end-of-feature.
- Acceptance Criteria stays — write 3-7 testable AC bullets, no REQ IDs. If the architect
  was skipped, the planner emits light ACs; if the planner was also skipped, the verifier
  derives what to check from the diff.

### ADR ceremony — deferred by default

- Do NOT auto-write ADR fragments under `.ai-state/decisions/drafts/`.
- IF the user explicitly says "write an ADR for X" — use the direct-tier path
  (`.ai-state/decisions/<NNN>-<slug>.md`, no fragment, no draft lifecycle).
- The `remind_adr.py` hook's advisory warning is silenced; its check still runs.

### Test discipline — RELAXED

- Implementer writes production code AND a happy-path smoke test in the same step.
- test-engineer is invoked only on explicit request (property/contract/integration suites).
- Tests still run; `pytest` failures still surface honestly — but a red test is a WARN,
  not a FAIL, and does NOT gate the verifier or the pipeline. A happy-path smoke test is
  still expected; its ABSENCE for new behavior is also a WARN.

### Slim artifact shapes

- **Architect (`SYSTEMS_PLAN.md`):** Surface Assumptions, Registered Objections, Goals &
  Non-Goals, Context (1 para), Architecture (Overview, Components, Data Flow if
  non-trivial), Acceptance Criteria, Risks (top 3), Out-of-scope. Skip: Behavioral
  Specification, ADR fragment, tech-debt sweep, Tier-2 Stakeholder Review, DESIGN.md /
  docs/architecture.md updates.
- **Planner (`IMPLEMENTATION_PLAN.md`):** numbered steps + file paths + per-step
  acceptance. WIP.md and LEARNINGS.md still produced. No traceability.yml, no REQ IDs,
  no paired test-engineer step required. Coarser decomposition (3-5 steps for 4-8 files
  is fine). If the architect was skipped, add a short top-level "what 'done' means" list.
- **Verifier (`VERIFICATION_REPORT.md`):** Phases 1, 2, 3 (AC), 5 (lint/typecheck),
  5.5 (Behavioral Contract), 10 (test status), 12 (report). Auto-skip 4, 7, 8, 9, 11.
  FAIL: lint/typecheck/behavioral-contract failure. WARN (not FAIL): a failing or
  absent test. The report header records the entry point and the skipped stages.

### To exit hackathon mode

Set `PRAXION_HACKATHON_MODE=0`, delete this `## Hackathon Mode` block from `CLAUDE.md`,
remove the `hackathon` preset from `.claude/praxion-rules.yaml`, and stop launching via
the `praxion-hackathon` wrapper. Subsequent sessions resume the full 5-tier process.

## Agent Pipeline

Follow the **Understand, Plan, Verify** methodology. For multi-step work (Standard/Full tier), delegate to specialized agents in pipeline order. Each pipeline operates in an ephemeral `.ai-work/<task-slug>/` directory (deleted after use); permanent artifacts go to `.ai-state/` (committed to git).

1. **researcher** → `.ai-work/<slug>/RESEARCH_FINDINGS.md` — codebase exploration, external docs
2. **systems-architect** → `.ai-work/<slug>/SYSTEMS_PLAN.md` + ADR drafts under `.ai-state/decisions/drafts/` (promoted to stable `<NNN>-<slug>.md` once on `main` by the finalize hook chain — post-merge / post-commit / post-checkout, all sharing one dispatcher) + `.ai-state/DESIGN.md` (architect-facing) + `docs/architecture.md` (developer-facing)
3. **implementation-planner** → `.ai-work/<slug>/IMPLEMENTATION_PLAN.md` + `WIP.md` — step decomposition
4. **implementer** + **test-engineer** (concurrent, on disjoint file sets) → code + tests — execute steps from the plan
5. **verifier** → `.ai-work/<slug>/VERIFICATION_REPORT.md` — post-implementation review

**Recognized pipeline branches.** The pipeline is not strictly linear. The architect's pre-refactor assessment may emit `.ai-work/<slug>/PRE_REFACTOR_PLAN.md`, which triggers a same-worktree mini-pipeline (characterization-tests-first → implementer → orchestrator-mediated verifier-vs-loopback decision) before the parent feature plan resumes. Re-entry is bounded at one pass via the architect's `post-refactor-adaptation` mode. The mini-pipeline reuses the existing `[Phase: Refactoring]` tag — no new tag is invented.

**Independent audits.** The `sentinel` agent runs outside the pipeline and writes timestamped `.ai-state/sentinel_reports/SENTINEL_REPORT_<timestamp>.md` plus an append-only `.ai-state/sentinel_reports/SENTINEL_LOG.md`. Trigger it for ecosystem health baselines (before first ideation, after major refactors).

**From PoC to production.** The feature pipeline is one milestone of many. The full journey: baseline audit (`/sentinel`) → CI/CD setup (`cicd-engineer` agent) → deployment (`deployment` skill) → first release (`/release`) → ongoing decisions captured as ADRs in `.ai-state/decisions/` → cross-session memory in `.ai-state/memory.json` (when memory MCP is enabled).

Always include expected deliverables when delegating to an agent. The agent coordination protocol rule has full delegation checklists.

## Compaction Guidance

When this conversation compacts, always preserve: the active pipeline stage and task slug, the current WIP step number and status, acceptance criteria from the systems plan, and the list of files modified in the current step. The Praxion `PreCompact` hook snapshots in-flight pipeline documents to `.ai-work/<slug>/PIPELINE_STATE.md` — re-read that file after compaction to restore orientation.

## Behavioral Contract

Four non-negotiable behaviors for any agent (including Claude itself) writing, planning, or reviewing code:

- **Surface Assumptions** — state your interpretation up front and surface gap-filling assumptions as you make them; a plausible default never *feels* like ambiguity. Pause when one is load-bearing and hard to reverse.
- **Register Objection** — when a request violates scope, structure, or evidence, state the conflict with a reason before complying or declining.
- **Stay Surgical** — touch only what the change requires; if scope grew, stop and re-scope instead of expanding silently.
- **Simplicity First** — prefer the smallest solution that meets the behavior; every line, file, or dependency must earn its place.

Self-test: did I state my assumptions, flag conflicts with reasons, stay in scope, and pick the simplest path?

## Praxion Process

Apply Praxion's tier-driven pipeline for non-trivial work. Use the tier selector from `rules/swe/swe-agent-coordination-protocol.md`: Direct (single-file fix/typo) or Lightweight (2–3 files) may skip the full pipeline; Standard or Full tier work requires researcher → systems-architect → implementation-planner → implementer + test-engineer → verifier.

**Rule-inheritance corollary.** When delegating to any subagent — Praxion-native or host-native (Explore, Plan, general-purpose) — carry the behavioral contract into every delegation prompt. Host-native subagents do not load CLAUDE.md; the orchestrator is the only delivery path.

**Orchestrator obligation.** Every delegation prompt must name the task slug, expected deliverables, and the behavioral contract (Surface Assumptions · Register Objection · Stay Surgical · Simplicity First).

## Working in this project

This `CLAUDE.md` is the **index**; `docs/` and the skills it points to are the **library** — read the index, follow the links the task needs. When I correct you, propose a durable rule for review (a memory entry, a `CLAUDE.md` or rule edit, or a skill note) so the correction outlasts this session.

### Verification

After every change, run these in order — fix at each step before moving on:

1. `ty check sia/`
2. `python -m pytest tests/ -v`
3. `ruff check sia/ tests/` and `ruff format --check sia/ tests/`
4. `python -m build --sdist --wheel`

### Frequent operations

You'll most often be asked to:

- Add or modify benchmark tasks under `sia/tasks/` (task spec, reference agents, datasets)
- Adjust the orchestrator generation loop and Meta/Target/Feedback agent coordination (`sia/orchestrator.py`)
- Tune agent execution, sandboxing, or context management (`sia/util.py`, `sia/context_manager.py`)
- Wire up or debug an agent-SDK backend (`claude`, `openhands`, `mlebench` extras)
- Update evaluation/scoring logic and run benchmark tasks
