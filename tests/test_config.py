"""Unit tests for sia.config.Config."""

from dataclasses import fields

from sia.config import Config


def test_default_values():
    cfg = Config()
    assert cfg.DEFAULT_MAX_GENERATIONS == 3
    assert cfg.DEFAULT_AGENT_IMPL == "claude"
    assert cfg.SANDBOX_MODE == "none"
    assert cfg.DEFAULT_MAX_TURNS == 20
    assert cfg.DOCKER_MEMORY_LIMIT == "2g"
    assert cfg.MAX_CONTEXT_FILE_SIZE == 10_000_000


def test_from_env_reads_sia_vars(monkeypatch):
    monkeypatch.setenv("SIA_MAX_GENERATIONS", "5")
    monkeypatch.setenv("SIA_AGENT_IMPL", "openhands")
    monkeypatch.setenv("SIA_SANDBOX_MODE", "docker")
    monkeypatch.setenv("SIA_META_MODEL", "opus")

    cfg = Config.from_env()
    assert cfg.DEFAULT_MAX_GENERATIONS == 5
    assert cfg.DEFAULT_AGENT_IMPL == "openhands"
    assert cfg.SANDBOX_MODE == "docker"
    assert cfg.DEFAULT_CLAUDE_META_MODEL == "opus"


def test_from_env_invalid_value_keeps_default(monkeypatch):
    monkeypatch.setenv("SIA_MAX_GENERATIONS", "not-a-number")

    cfg = Config.from_env()
    assert cfg.DEFAULT_MAX_GENERATIONS == 3


def test_from_env_no_vars_returns_defaults():
    cfg = Config.from_env()
    assert cfg.DEFAULT_MAX_GENERATIONS == 3
    assert cfg.DEFAULT_TASK_MODEL == "claude-haiku-4-5-20251001"


def test_config_is_dataclass_with_expected_fields():
    field_names = {f.name for f in fields(Config)}
    expected = {
        "DEFAULT_CLAUDE_META_MODEL",
        "DEFAULT_TASK_MODEL",
        "DEFAULT_MAX_GENERATIONS",
        "DEFAULT_AGENT_IMPL",
        "SANDBOX_MODE",
        "DOCKER_IMAGE",
        "MAX_CONTEXT_FILE_SIZE",
    }
    assert expected.issubset(field_names)


def test_harness_amplification_knob_defaults():
    cfg = Config()
    assert cfg.FAILURE_TAXONOMY is True
    assert cfg.FAILURE_TAXONOMY_TOP_N == 5
    assert cfg.VERIFIER_PASS_STATUSES == ("CORRECT", "PASS", "correct")
    assert cfg.CHANGE_LIBRARY is True
    assert cfg.CHANGE_LIBRARY_PATH == "reference/change_library.md"
    assert cfg.BASE_ON_BEST is True
    assert cfg.REJECT_REGRESSION is True
    assert cfg.REGRESSION_REPROMPT_MAX == 1
    assert cfg.ALLOW_TASK_FAMILY_SPECIALIZATION is True


def test_credit_assignment_knobs_are_absent():
    # Plan 3 (scored credit assignment) is deferred; its knobs must not exist yet.
    field_names = {f.name for f in fields(Config)}
    assert not any(name.startswith("CREDIT_") for name in field_names)


def test_from_env_overrides_failure_taxonomy(monkeypatch):
    monkeypatch.setenv("SIA_FAILURE_TAXONOMY", "off")
    monkeypatch.setenv("SIA_FAILURE_TAXONOMY_TOP_N", "9")

    cfg = Config.from_env()
    assert cfg.FAILURE_TAXONOMY is False
    assert cfg.FAILURE_TAXONOMY_TOP_N == 9


def test_from_env_overrides_verifier_pass_statuses_tuple(monkeypatch):
    monkeypatch.setenv("SIA_VERIFIER_PASS_STATUSES", "OK, GREEN ,done")

    cfg = Config.from_env()
    assert cfg.VERIFIER_PASS_STATUSES == ("OK", "GREEN", "done")


def test_from_env_overrides_change_library(monkeypatch):
    monkeypatch.setenv("SIA_CHANGE_LIBRARY", "no")
    monkeypatch.setenv("SIA_CHANGE_LIBRARY_PATH", "ref/custom_library.md")

    cfg = Config.from_env()
    assert cfg.CHANGE_LIBRARY is False
    assert cfg.CHANGE_LIBRARY_PATH == "ref/custom_library.md"


def test_from_env_overrides_base_on_best_and_regression(monkeypatch):
    monkeypatch.setenv("SIA_BASE_ON_BEST", "false")
    monkeypatch.setenv("SIA_REJECT_REGRESSION", "0")
    monkeypatch.setenv("SIA_REGRESSION_REPROMPT_MAX", "3")

    cfg = Config.from_env()
    assert cfg.BASE_ON_BEST is False
    assert cfg.REJECT_REGRESSION is False
    assert cfg.REGRESSION_REPROMPT_MAX == 3


def test_from_env_overrides_allow_task_family_specialization(monkeypatch):
    monkeypatch.setenv("SIA_ALLOW_TASK_FAMILY_SPECIALIZATION", "off")

    cfg = Config.from_env()
    assert cfg.ALLOW_TASK_FAMILY_SPECIALIZATION is False


def test_from_env_invalid_bool_knob_keeps_default(monkeypatch):
    monkeypatch.setenv("SIA_FAILURE_TAXONOMY", "maybe")

    cfg = Config.from_env()
    assert cfg.FAILURE_TAXONOMY is True


def test_from_env_no_amplification_vars_keeps_defaults():
    cfg = Config.from_env()
    assert cfg.FAILURE_TAXONOMY is True
    assert cfg.VERIFIER_PASS_STATUSES == ("CORRECT", "PASS", "correct")
    assert cfg.BASE_ON_BEST is True


def test_best_of_n_knob_defaults():
    cfg = Config()
    assert cfg.BEST_OF_N == 1  # 1 == OFF (today's single-candidate loop)
    assert cfg.BEST_OF_N_SELECTION == "accuracy"
    assert cfg.BEST_OF_N_TIEBREAK == "smaller_code"


def test_minibatch_frac_knob_is_absent():
    # v1 best-of-N is full-eval argmax; the minibatch knob is dead config until the
    # minibatch follow-up ships.
    field_names = {f.name for f in fields(Config)}
    assert not any("MINIBATCH" in name for name in field_names)


def test_from_env_overrides_best_of_n(monkeypatch):
    monkeypatch.setenv("SIA_BEST_OF_N", "3")
    monkeypatch.setenv("SIA_BEST_OF_N_SELECTION", "accuracy")
    monkeypatch.setenv("SIA_BEST_OF_N_TIEBREAK", "smaller_code")

    cfg = Config.from_env()
    assert cfg.BEST_OF_N == 3
    assert isinstance(cfg.BEST_OF_N, int)
    assert cfg.BEST_OF_N_SELECTION == "accuracy"
    assert cfg.BEST_OF_N_TIEBREAK == "smaller_code"


def test_from_env_invalid_best_of_n_keeps_default(monkeypatch):
    monkeypatch.setenv("SIA_BEST_OF_N", "three")

    cfg = Config.from_env()
    assert cfg.BEST_OF_N == 1


def test_from_env_no_best_of_n_var_keeps_default():
    cfg = Config.from_env()
    assert cfg.BEST_OF_N == 1


def test_best_of_n_concurrency_knob_defaults():
    cfg = Config()
    assert cfg.BEST_OF_N_CONCURRENCY == 0  # 0 == all-K parallel
    assert cfg.BEST_OF_N_STAGGER_SECONDS == 1.0
    assert isinstance(cfg.BEST_OF_N_STAGGER_SECONDS, float)


def test_from_env_overrides_best_of_n_concurrency(monkeypatch):
    monkeypatch.setenv("SIA_BEST_OF_N_CONCURRENCY", "2")
    monkeypatch.setenv("SIA_BEST_OF_N_STAGGER_SECONDS", "0.5")

    cfg = Config.from_env()
    assert cfg.BEST_OF_N_CONCURRENCY == 2
    assert isinstance(cfg.BEST_OF_N_CONCURRENCY, int)
    assert cfg.BEST_OF_N_STAGGER_SECONDS == 0.5
    assert isinstance(cfg.BEST_OF_N_STAGGER_SECONDS, float)


def test_from_env_invalid_concurrency_knobs_keep_defaults(monkeypatch):
    monkeypatch.setenv("SIA_BEST_OF_N_CONCURRENCY", "lots")
    monkeypatch.setenv("SIA_BEST_OF_N_STAGGER_SECONDS", "soon")

    cfg = Config.from_env()
    assert cfg.BEST_OF_N_CONCURRENCY == 0
    assert cfg.BEST_OF_N_STAGGER_SECONDS == 1.0


def test_from_env_sia_max_turns_overrides_instance_default(monkeypatch):
    """SIA_MAX_TURNS=40 yields an instance DEFAULT_MAX_TURNS of 40 (class default stays 20)."""
    monkeypatch.setenv("SIA_MAX_TURNS", "40")

    cfg = Config.from_env()
    assert cfg.DEFAULT_MAX_TURNS == 40
    # The class attribute is untouched — only the instance carries the override.
    assert Config.DEFAULT_MAX_TURNS == 20
