"""`metricguard doctor` — the pure provider->key mapping it diagnoses with."""

from metricguard.cli import _llm_key_env


def test_known_providers_map_to_their_key_env():
    assert _llm_key_env("anthropic:claude-opus-4-8") == "ANTHROPIC_API_KEY"
    assert _llm_key_env("openai:gpt-4o") == "OPENAI_API_KEY"
    assert _llm_key_env("google_genai:gemini-3.5-flash") == "GOOGLE_API_KEY"


def test_unknown_or_unprefixed_models_return_none():
    assert _llm_key_env("gpt-4o") is None            # no provider prefix
    assert _llm_key_env("acme:frontier-1") is None   # unknown provider
    assert _llm_key_env("") is None
