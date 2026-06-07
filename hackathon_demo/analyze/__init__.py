"""R2 improvement-taxonomy classifier (read-only over `runs/<run>/gen_*/improvement.md`).

The single source of truth for the 6 improvement buckets lives in `taxonomy.py`;
`analyze_improvements.py` classifies each generation's `improvement.md` into a
per-change list (LLM-primary on Nebius, deterministic keyword fallback offline).
"""
