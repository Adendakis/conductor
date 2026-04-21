"""Step metrics and cost calculation."""

from pydantic import BaseModel


# Pricing per 1M tokens: (input, output)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "anthropic.claude-sonnet-4-20250514": (3.0, 15.0),
    "anthropic.claude-haiku-3-20250310": (0.25, 1.25),
    "anthropic.claude-opus-4-20250514": (15.0, 75.0),
}


class StepMetrics(BaseModel):
    """Metrics for a single step execution."""

    step_id: str = ""
    agent_name: str = ""
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    requests: int = 0
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0

    def to_log_line(self) -> str:
        return (
            f"[{self.agent_name}] {self.model_id} "
            f"in={self.input_tokens:,} out={self.output_tokens:,} "
            f"${self.cost_usd:.4f} ({self.elapsed_seconds:.1f}s)"
        )


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    cache_write: int = 0,
    cache_read: int = 0,
) -> float:
    """Calculate USD cost for a model invocation."""
    prices = MODEL_PRICING.get(model_id, (3.0, 15.0))
    input_cost = (input_tokens / 1_000_000) * prices[0]
    output_cost = (output_tokens / 1_000_000) * prices[1]
    cache_write_cost = (cache_write / 1_000_000) * prices[0] * 1.25
    cache_read_cost = (cache_read / 1_000_000) * prices[0] * 0.1
    return input_cost + output_cost + cache_write_cost + cache_read_cost
