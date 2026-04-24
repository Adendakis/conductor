"""Tests for the provider pool and factory."""

from pathlib import Path
from unittest.mock import MagicMock

from conductor.providers.base import LLMProvider, LLMResponse, ModelConfig, AgentLoopResponse
from conductor.providers.pool import LabeledProvider, ProviderMetrics, ProviderPool


def _mock_provider(label: str, success: bool = True, error: str = None) -> LabeledProvider:
    """Create a mock labeled provider."""
    provider = MagicMock(spec=LLMProvider)
    if success:
        provider.call.return_value = LLMResponse(success=True, content="ok", model_id="test")
        provider.run_agent_loop.return_value = AgentLoopResponse(completed=True, final_text="done")
    else:
        provider.call.side_effect = Exception(error or "provider failed")
        provider.run_agent_loop.side_effect = Exception(error or "provider failed")
    return LabeledProvider(
        provider=provider, label=label, metrics=ProviderMetrics(label=label)
    )


def test_fallback_uses_first_provider():
    """Fallback strategy uses the first provider when it succeeds."""
    p1 = _mock_provider("primary", success=True)
    p2 = _mock_provider("secondary", success=True)
    pool = ProviderPool([p1, p2], strategy="fallback")

    result = pool.call("sys", "user", ModelConfig())
    assert result.success
    p1.provider.call.assert_called_once()
    p2.provider.call.assert_not_called()


def test_fallback_tries_next_on_failure():
    """Fallback strategy tries the next provider when the first fails."""
    p1 = _mock_provider("primary", success=False, error="throttled")
    p2 = _mock_provider("secondary", success=True)
    pool = ProviderPool([p1, p2], strategy="fallback")

    result = pool.call("sys", "user", ModelConfig())
    assert result.success
    assert p1.metrics.failure_count == 1
    assert p2.metrics.success_count == 1


def test_all_providers_fail():
    """Returns error when all providers fail."""
    p1 = _mock_provider("a", success=False, error="fail-a")
    p2 = _mock_provider("b", success=False, error="fail-b")
    pool = ProviderPool([p1, p2], strategy="fallback")

    result = pool.call("sys", "user", ModelConfig())
    assert not result.success
    assert "All 2 providers failed" in result.error


def test_preferred_provider():
    """preferred_provider in ModelConfig routes to that provider first."""
    p1 = _mock_provider("primary", success=True)
    p2 = _mock_provider("secondary", success=True)
    pool = ProviderPool([p1, p2], strategy="fallback")

    config = ModelConfig(preferred_provider="secondary")
    result = pool.call("sys", "user", config)
    assert result.success
    # Secondary should be called first
    p2.provider.call.assert_called_once()
    p1.provider.call.assert_not_called()


def test_round_robin():
    """Round robin distributes calls across providers."""
    p1 = _mock_provider("a", success=True)
    p2 = _mock_provider("b", success=True)
    pool = ProviderPool([p1, p2], strategy="round_robin")

    pool.call("sys", "user", ModelConfig())
    pool.call("sys", "user", ModelConfig())

    assert p1.provider.call.call_count == 1
    assert p2.provider.call.call_count == 1


def test_metrics_tracking():
    """Pool tracks per-provider metrics."""
    p1 = _mock_provider("primary", success=True)
    pool = ProviderPool([p1], strategy="fallback")

    pool.call("sys", "user", ModelConfig())
    pool.call("sys", "user", ModelConfig())

    metrics = pool.get_pool_metrics()
    assert len(metrics) == 1
    assert metrics[0]["label"] == "primary"
    assert metrics[0]["calls"] == 2
    assert metrics[0]["successes"] == 2


def test_get_by_label():
    """Can retrieve a provider by label."""
    p1 = _mock_provider("alpha")
    p2 = _mock_provider("beta")
    pool = ProviderPool([p1, p2])

    assert pool.get_by_label("beta") is p2
    assert pool.get_by_label("nonexistent") is None


def test_factory_single_provider(tmp_path):
    """Factory creates a single provider from config."""
    from conductor.providers.factory import build_provider_from_config

    # This will fail because boto3 might not be configured,
    # but it should at least attempt the right provider type
    config = {"providers": {"type": "bedrock", "region": "us-east-1"}}
    try:
        provider = build_provider_from_config(config)
        assert provider is not None
    except ImportError:
        pass  # boto3 not installed — that's fine for this test


def test_factory_unknown_type():
    """Factory raises on unknown provider type."""
    from conductor.providers.factory import build_provider_from_config
    import pytest

    config = {"providers": {"type": "nonexistent"}}
    with pytest.raises(ValueError, match="Unknown provider type"):
        build_provider_from_config(config)


def test_factory_no_config():
    """Factory returns None when no provider config."""
    from conductor.providers.factory import build_provider_from_config

    assert build_provider_from_config({}) is None
    assert build_provider_from_config({"other": "stuff"}) is None
