"""Provider pool — routes LLM calls across multiple providers."""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .base import AgentLoopResponse, LLMProvider, LLMResponse, ModelConfig

log = logging.getLogger("conductor.providers.pool")


@dataclass
class ProviderMetrics:
    """Tracks per-provider health metrics."""

    label: str = ""
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    throttle_count: int = 0
    total_latency: float = 0.0
    last_failure_time: float = 0.0

    @property
    def avg_latency(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency / self.call_count

    @property
    def failure_rate(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.failure_count / self.call_count


@dataclass
class LabeledProvider:
    """A provider with a label and metrics."""

    provider: LLMProvider
    label: str = ""
    metrics: ProviderMetrics = field(default_factory=ProviderMetrics)


class ProviderPool(LLMProvider):
    """Routes LLM calls across multiple providers with failover.

    Strategies:
    - fallback: try providers in order, move to next on failure/throttle
    - round_robin: distribute calls evenly across providers
    """

    def __init__(
        self,
        providers: list[LabeledProvider],
        strategy: str = "fallback",
    ):
        if not providers:
            raise ValueError("ProviderPool requires at least one provider")
        self.providers = providers
        self.strategy = strategy
        self._rr_index = 0  # for round_robin

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model_config: ModelConfig,
    ) -> LLMResponse:
        """Single LLM call with failover."""
        ordered = self._select_order(model_config)

        last_error = None
        for lp in ordered:
            start = time.time()
            lp.metrics.call_count += 1
            try:
                response = lp.provider.call(system_prompt, user_prompt, model_config)
                elapsed = time.time() - start
                lp.metrics.total_latency += elapsed

                if response.success:
                    lp.metrics.success_count += 1
                    log.debug(f"Provider '{lp.label}' call succeeded ({elapsed:.1f}s)")
                    return response
                else:
                    lp.metrics.failure_count += 1
                    lp.metrics.last_failure_time = time.time()
                    last_error = response.error
                    log.warning(
                        f"Provider '{lp.label}' returned error: {response.error}"
                    )
                    continue

            except Exception as e:
                elapsed = time.time() - start
                lp.metrics.total_latency += elapsed
                lp.metrics.failure_count += 1
                lp.metrics.last_failure_time = time.time()
                last_error = str(e)

                err_lower = str(e).lower()
                if any(k in err_lower for k in ("throttl", "too many", "rate")):
                    lp.metrics.throttle_count += 1
                    log.warning(f"Provider '{lp.label}' throttled, trying next")
                else:
                    log.error(f"Provider '{lp.label}' failed: {e}")
                continue

        return LLMResponse(
            success=False,
            error=f"All {len(ordered)} providers failed. Last error: {last_error}",
            model_id=model_config.model_id,
        )

    def run_agent_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list,
        model_config: ModelConfig,
        working_directory: Path,
        max_iterations: int = 50,
        sandbox_overrides: dict | None = None,
    ) -> AgentLoopResponse:
        """Agent loop with failover — tries the full loop on each provider."""
        ordered = self._select_order(model_config)

        last_error = None
        for lp in ordered:
            start = time.time()
            lp.metrics.call_count += 1
            try:
                response = lp.provider.run_agent_loop(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    tools=tools,
                    model_config=model_config,
                    working_directory=working_directory,
                    max_iterations=max_iterations,
                    sandbox_overrides=sandbox_overrides,
                )
                elapsed = time.time() - start
                lp.metrics.total_latency += elapsed

                if response.completed:
                    lp.metrics.success_count += 1
                    return response
                else:
                    lp.metrics.failure_count += 1
                    lp.metrics.last_failure_time = time.time()
                    last_error = response.error
                    log.warning(f"Provider '{lp.label}' agent loop failed: {response.error}")
                    continue

            except Exception as e:
                elapsed = time.time() - start
                lp.metrics.total_latency += elapsed
                lp.metrics.failure_count += 1
                lp.metrics.last_failure_time = time.time()
                last_error = str(e)
                log.error(f"Provider '{lp.label}' agent loop error: {e}")
                continue

        return AgentLoopResponse(
            completed=False,
            error=f"All {len(ordered)} providers failed. Last error: {last_error}",
        )

    def _select_order(self, model_config: ModelConfig) -> list[LabeledProvider]:
        """Determine provider order based on strategy and preferences."""
        # Check for preferred provider
        preferred = getattr(model_config, "preferred_provider", None)
        if preferred:
            pref_lp = self.get_by_label(preferred)
            if pref_lp:
                others = [lp for lp in self.providers if lp.label != preferred]
                return [pref_lp] + others

        if self.strategy == "round_robin":
            n = len(self.providers)
            idx = self._rr_index % n
            self._rr_index += 1
            return self.providers[idx:] + self.providers[:idx]

        # Default: fallback (try in order)
        return list(self.providers)

    # --- Query methods for agent-driven selection ---

    def get_by_label(self, label: str) -> Optional[LabeledProvider]:
        """Get a specific provider by its config label."""
        for lp in self.providers:
            if lp.label == label:
                return lp
        return None

    def list_labels(self) -> list[str]:
        """List labels of all providers."""
        return [lp.label for lp in self.providers]

    def get_pool_metrics(self) -> list[dict]:
        """Get metrics for all providers."""
        return [
            {
                "label": lp.label,
                "calls": lp.metrics.call_count,
                "successes": lp.metrics.success_count,
                "failures": lp.metrics.failure_count,
                "throttles": lp.metrics.throttle_count,
                "avg_latency": round(lp.metrics.avg_latency, 2),
                "failure_rate": round(lp.metrics.failure_rate, 3),
            }
            for lp in self.providers
        ]
