"""Happy-path smoke test for the weak Gen-1 NL->SQL target scaffold.

The scaffold is a standalone script inside the task folder (excluded from
lint/type-check like sia/tasks/), so it is loaded here by file path via importlib
rather than imported as a package module -- mirroring test_grader.py.

ZERO-SPEND CONTRACT: these tests only *construct* the openai client and exercise
pure helpers (prompt building, model resolution). They never call
chat.completions.create -- no network, no API key, no spend. The live call is
the orchestrator's concern, not this smoke test's.
"""

import importlib.util
from pathlib import Path

import pytest

_SCAFFOLD_PATH = Path(__file__).resolve().parent.parent / "sql_task" / "reference" / "reference_target_agent.py"


def _load_scaffold():
    spec = importlib.util.spec_from_file_location("sql_target_agent", _SCAFFOLD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scaffold():
    return _load_scaffold()


def test_scaffold_module_imports(scaffold):
    assert hasattr(scaffold, "build_client")
    assert hasattr(scaffold, "generate_sql")


def test_client_constructs_with_dummy_env(scaffold, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_BASE", "https://api.tokenfactory.nebius.com/v1/")
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy-key-not-used-no-network")

    client = scaffold.build_client()

    # The client builds and is pointed at Token Factory; no request is sent.
    assert client is not None
    assert "tokenfactory.nebius.com" in str(client.base_url)


def test_build_client_requires_base_url(scaffold, monkeypatch):
    monkeypatch.delenv("NEBIUS_API_BASE", raising=False)
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy-key")

    with pytest.raises(RuntimeError, match="NEBIUS_API_BASE"):
        scaffold.build_client()


def test_build_client_requires_api_key(scaffold, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_BASE", "https://api.tokenfactory.nebius.com/v1/")
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        scaffold.build_client()


def test_resolve_model_reads_env_override(scaffold, monkeypatch):
    monkeypatch.setenv("SIA_TASK_MODEL", "some/resolved-oss-id")
    assert scaffold.resolve_model() == "some/resolved-oss-id"


def test_resolve_model_falls_back_to_sentinel_when_unset(scaffold, monkeypatch):
    monkeypatch.delenv("SIA_TASK_MODEL", raising=False)
    assert scaffold.resolve_model() == scaffold.UNRESOLVED_MODEL_SENTINEL


def test_prompt_contains_question_and_table_names_only(scaffold):
    prompt = scaffold.build_prompt("How many singers do we have?", ["singer", "concert", "stadium"])

    assert "How many singers do we have?" in prompt
    assert "singer" in prompt
    # The weak-baseline contract: no schema DDL leaks into the prompt.
    assert "CREATE TABLE" not in prompt


def test_table_names_read_from_db_id_database(scaffold):
    # Routing check: the scaffold reads bare table names from data/public/<db_id>.sqlite.
    public_dir = Path(__file__).resolve().parent.parent / "sql_task" / "data" / "public"
    names = scaffold.table_names_for_db(public_dir, "concert_singer")
    assert "singer" in names
    # No DDL is returned -- just bare table names.
    assert all("CREATE" not in n.upper() for n in names)
