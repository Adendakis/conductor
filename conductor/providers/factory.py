"""Provider factory — creates LLM providers from configuration."""

import logging
from typing import Optional

from .base import LLMProvider
from .pool import LabeledProvider, ProviderMetrics, ProviderPool

log = logging.getLogger("conductor.providers.factory")

# Registry of known provider types → (module_path, class_name, install_extra)
_PROVIDER_TYPES = {
    "bedrock": (
        "conductor.providers.bedrock",
        "BedrockProvider",
        "conductor[bedrock]",
    ),
    # Future providers:
    # "openai": ("conductor.providers.openai_provider", "OpenAIProvider", "conductor[openai]"),
    # "anthropic": ("conductor.providers.anthropic_provider", "AnthropicProvider", "conductor[anthropic]"),
}


def build_provider_from_config(config: dict) -> Optional[LLMProvider]:
    """Create an LLM provider (or pool) from configuration.

    Config formats:

    Single provider:
        providers:
          type: "bedrock"
          region: "us-east-1"

    Provider pool:
        providers:
          pool:
            strategy: "fallback"
            providers:
              - type: "bedrock"
                region: "us-east-1"
                label: "primary"
              - type: "bedrock"
                region: "us-west-2"
                label: "secondary"

    Returns None if no provider config is found.
    """
    providers_config = config.get("providers", config.get("provider", {}))

    if not providers_config:
        return None

    # Pool configuration
    if "pool" in providers_config:
        pool_config = providers_config["pool"]
        strategy = pool_config.get("strategy", "fallback")
        entries = pool_config.get("providers", [])

        if not entries:
            log.warning("Provider pool configured but no providers listed")
            return None

        labeled_providers = []
        for i, entry in enumerate(entries):
            label = entry.get("label", f"provider-{i}")
            try:
                provider = _create_single_provider(entry)
                labeled_providers.append(
                    LabeledProvider(
                        provider=provider,
                        label=label,
                        metrics=ProviderMetrics(label=label),
                    )
                )
                log.info(f"Pool: added provider '{label}' (type={entry.get('type')})")
            except Exception as e:
                log.error(f"Pool: failed to create provider '{label}': {e}")
                # Continue — pool works with remaining providers

        if not labeled_providers:
            log.error("No providers could be created for the pool")
            return None

        pool = ProviderPool(labeled_providers, strategy=strategy)
        log.info(
            f"Provider pool created: {len(labeled_providers)} providers, "
            f"strategy={strategy}"
        )
        return pool

    # Single provider configuration
    if "type" in providers_config:
        try:
            provider = _create_single_provider(providers_config)
            log.info(f"Provider created: type={providers_config['type']}")
            return provider
        except Exception as e:
            log.error(f"Failed to create provider: {e}")
            raise

    return None


def _create_single_provider(entry: dict) -> LLMProvider:
    """Create a single provider instance from a config entry."""
    ptype = entry.get("type", "")

    if ptype not in _PROVIDER_TYPES:
        available = ", ".join(_PROVIDER_TYPES.keys())
        raise ValueError(
            f"Unknown provider type: '{ptype}'. Available: {available}"
        )

    module_path, class_name, install_extra = _PROVIDER_TYPES[ptype]

    try:
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    except ImportError as e:
        raise ImportError(
            f"Provider '{ptype}' requires additional dependencies. "
            f"Install with: pip install {install_extra}"
        ) from e

    # Pass config fields as kwargs (provider-specific)
    kwargs = {}
    if "region" in entry:
        kwargs["region"] = entry["region"]

    return cls(**kwargs)
