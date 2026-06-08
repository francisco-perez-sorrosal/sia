# Task-Family Change Library: Natural-Language-to-SQL (Spider)

This is a curated set of **proven, task-family-level** harness patterns for the
multi-database NL→SQL task family. It is front-loaded into the meta-agent's gen-1
prompt so the initial scaffold starts where prior self-improving runs converged —
instead of rediscovering these patterns over multiple generations.

**Scope guard:** every entry below is a *general SQL-generation heuristic* that
applies to any NL→SQL question against any relational schema. Nothing here names a
specific held-out question, a specific gold result, or any item-specific SQL. The
held-out questions and their gold answers live only behind the grader and must
never be encoded into the scaffold.

---

## 1. Adequate token budget + retry on empty/truncated output

Generate SQL with a generous completion-token budget (at least ~2048 tokens). A
small cap is the single most common cause of empty or mid-query-truncated output
on verbose models, and every truncated query scores zero. After each generation,
check that the response is non-empty and looks like a complete statement; if it is
empty or truncated, retry once with the same prompt. This robustness fix is cheap
and model-agnostic.

## 2. Schema enrichment: inject columns, foreign keys, and sample rows

Do not prompt the model with bare table names. Before generating SQL, introspect
the question's database (read-only) and include in the prompt, for each relevant
table: its column names and types, its foreign-key relationships, and a few
**sample rows** (or low-cardinality distinct values for categorical columns). The
sample values disambiguate how text is actually stored (casing, codes,
abbreviations) so the model filters on real literals rather than guessed ones.

## 3. Core SQL-correctness rule pack

Include these general correctness rules — each addresses a recurring failure mode
across NL→SQL questions, not any particular question:

- **Prefer subqueries over JOINs** when a question asks for rows compared against an
  aggregate (e.g. "below the average X"): compute the aggregate in a subquery
  rather than a self-join.
- **Use `DISTINCT` for one-to-many joins** only where the question implies unique
  entities and the join would otherwise duplicate rows — scope it narrowly, do not
  blanket every query.
- **`GROUP BY` must match the non-aggregated select columns exactly**; do not add
  extra grouping columns that change tie-breaking behavior.
- **Follow foreign-key chains exactly** when joining across multiple tables; join
  only the tables the question needs (minimal joins), not every reachable table.
- **Filter with `=` on known literals** rather than `LIKE` when the exact value is
  available from the schema sample rows.

## 4. Determinism: temperature 0

Call the model with `temperature=0`. This removes the class of "same question,
different answer on re-run" failures and makes both grading and self-improvement
reproducible.

## 5. Structured CHECKLIST output, not free-form chain-of-thought

Ask the model to fill a short, fixed **checklist** before emitting SQL (which
tables/columns are needed, which joins, which aggregate/filter, which ordering),
then output the final query. A structured checklist enforces the correctness rules
above. Avoid free-form chain-of-thought: it lets the model rationalize past the
rules and has been observed to *reduce* accuracy on this task family.

---

## Anti-patterns (do NOT front-load these)

- **Free-form chain-of-thought reasoning** — opens a rationalization channel that
  undermines the correctness rules.
- **Speculative tooling** such as graph-search join-path computation — added new
  failures in prior runs without net gain.
- **Blanket rules without scoping** — e.g. an unconditional "always GROUP BY the
  output column" rule caused incorrect tie-breaking; scope each rule to the case it
  addresses.
