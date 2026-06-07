#!/usr/bin/env python3
"""
Reference target agent (Gen-1, DELIBERATELY WEAK) for the multi-DB NL->SQL task.

This is the *seed* scaffold the meta-agent clones into gen_1/target_agent.py. Its
weakness is intentional and load-bearing: a measured chunk of the harder questions
must fail at Gen-1 so that later harness improvements (schema awareness, few-shot
examples, error-driven SQL self-repair) have ground to visibly recover. The things
this scaffold deliberately omits are exactly the improvements the Feedback-Agent is
meant to invent:

  - NO schema DDL in the prompt (only the bare table names of the question's db).
  - NO few-shot examples.
  - NO retry / self-repair loop around the SQL.
  - NO error handling around the generated SQL (it is written as-is; the grader
    scores any execution error as 0).

This task is MULTI-DATABASE: each held-out question carries a `db_id` and must be
answered against data/public/<db_id>.sqlite. The weak scaffold gives the model
only the bare table names of that question's database (read read-only from
sqlite_master) -- no columns, no DDL, no foreign keys.

The NL->SQL call runs on Nebius Token Factory via the raw `openai` SDK. The model
id is read from the environment (SIA_TASK_MODEL) so the framework's config override
flows through; the base_url and api_key come from the environment too.

Usage (the args the orchestrator passes):
    python reference_target_agent.py --dataset_dir ./data/public --working_dir ./output

Environment variables required at run time (NOT at import time):
    NEBIUS_API_KEY    Token Factory API key.
    NEBIUS_API_BASE   Token Factory base url (e.g. https://api.tokenfactory.nebius.com/v1/).
    SIA_TASK_MODEL    The OSS model id resolved from the live catalog.
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# Load a root .env (mirrors sia/tasks/_shared) so NEBIUS_* / SIA_TASK_MODEL are picked up.
load_dotenv()

# Placeholder model id used only when SIA_TASK_MODEL is unset, so an accidental
# run fails loudly with a clear message rather than silently calling a real model.
UNRESOLVED_MODEL_SENTINEL = "TODO-SET-SIA_TASK_MODEL"

MAX_TOKENS = 512


def build_client() -> OpenAI:
    """Construct the raw openai client pointed at Nebius Token Factory.

    Reads NEBIUS_API_BASE / NEBIUS_API_KEY from the environment. No network call
    is made here -- this only constructs the client object.
    """
    base_url = os.environ.get("NEBIUS_API_BASE")
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not base_url:
        raise RuntimeError("NEBIUS_API_BASE is not set (expected the Token Factory base url).")
    if not api_key:
        raise RuntimeError("NEBIUS_API_KEY is not set (expected a Token Factory API key).")
    return OpenAI(base_url=base_url, api_key=api_key)


def resolve_model() -> str:
    """Resolve the OSS model id from SIA_TASK_MODEL (pinned via .env)."""
    return os.environ.get("SIA_TASK_MODEL", UNRESOLVED_MODEL_SENTINEL)


def load_held_out_questions(dataset_dir: Path) -> list[dict[str, Any]]:
    """Load the held-out questions (question_id + question, NO gold) from sample.json."""
    sample_path = dataset_dir / "sample.json"
    if not sample_path.is_file():
        raise FileNotFoundError(f"Public sample not found at {sample_path}")
    with open(sample_path, encoding="utf-8") as f:
        sample = json.load(f)
    held_out = sample.get("held_out")
    if not held_out:
        raise ValueError("No 'held_out' questions found in sample.json")
    print(f"Loaded {len(held_out)} held-out questions from {sample_path}")
    return held_out


def table_names_for_db(dataset_dir: Path, db_id: str) -> list[str]:
    """Read the bare table names of a question's database (read-only).

    This is ALL the schema help the weak Gen-1 gives the model -- table names from
    sqlite_master, no columns, no DDL, no foreign keys. Opening the db read-only
    (mode=ro) keeps the agent unable to mutate the data.
    """
    db_path = dataset_dir / f"{db_id}.sqlite"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def build_prompt(question: str, table_names: list[str]) -> str:
    """The deliberately weak Gen-1 prompt: question + bare table names only.

    No schema DDL, no few-shot examples, no instruction to handle errors -- just
    enough to elicit a single SQL string. This is what the Feedback-Agent should
    later improve.
    """
    table_list = ", ".join(table_names)
    return (
        "Write a single SQLite query that answers the question below. "
        "The database has these tables: "
        f"{table_list}. "
        "Return ONLY the SQL, with no explanation and no markdown fences.\n\n"
        f"Question: {question}"
    )


def generate_sql(client: OpenAI, model: str, question: str, table_names: list[str]) -> str:
    """One bare NL->SQL call. No retry, no repair, no error handling around the SQL."""
    response = client.chat.completions.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": build_prompt(question, table_names)}],
    )
    return (response.choices[0].message.content or "").strip()


def run_inference(
    client: OpenAI, model: str, questions: list[dict[str, Any]], dataset_dir: Path, working_dir: Path
) -> None:
    """Generate one SQL per question (routed to its db_id) and write responses.json."""
    working_dir.mkdir(parents=True, exist_ok=True)
    submission: list[dict[str, Any]] = []
    # Cache table-name lookups so each db's sqlite_master is read at most once.
    tables_by_db: dict[str, list[str]] = {}

    print(f"\nGenerating SQL for {len(questions)} questions with model: {model}\n")
    for idx, q in enumerate(questions, 1):
        question_id = q["question_id"]
        question_text = q["question"]
        db_id = q["db_id"]
        if db_id not in tables_by_db:
            tables_by_db[db_id] = table_names_for_db(dataset_dir, db_id)
        print(f"[{idx}/{len(questions)}] {question_id} ({db_id}) ...", end=" ", flush=True)
        sql = generate_sql(client, model, question_text, tables_by_db[db_id])
        submission.append({"question_id": question_id, "sql": sql})
        print("done")

    responses_file = working_dir / "responses.json"
    with open(responses_file, "w", encoding="utf-8") as f:
        json.dump(submission, f, indent=2)
    print(f"\nWrote {len(submission)} candidate queries to {responses_file}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Weak Gen-1 NL->SQL target agent (Nebius Token Factory)")
    parser.add_argument("--dataset_dir", type=Path, required=True, help="Directory containing sample.json")
    parser.add_argument("--working_dir", type=Path, required=True, help="Directory to write responses.json")
    args = parser.parse_args()

    if not args.dataset_dir.exists():
        print(f"Error: dataset directory does not exist: {args.dataset_dir}")
        return 1

    model = resolve_model()
    if model == UNRESOLVED_MODEL_SENTINEL:
        print("Error: SIA_TASK_MODEL is not set. Set the concrete Nebius OSS id via .env.")
        return 1

    client = build_client()
    questions = load_held_out_questions(args.dataset_dir)
    run_inference(client, model, questions, args.dataset_dir, args.working_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
