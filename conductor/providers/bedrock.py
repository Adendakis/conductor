"""AWS Bedrock Converse API provider."""

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conductor.models.metrics import StepMetrics, calculate_cost

from .base import AgentLoopResponse, LLMProvider, LLMResponse, ModelConfig

if TYPE_CHECKING:
    from conductor.tools.base import AgentTool


class BedrockProvider(LLMProvider):
    """AWS Bedrock Converse API provider.

    Handles:
    - Model invocation via boto3 bedrock-runtime client
    - Exponential backoff on throttling
    - Token usage tracking per call
    """

    def __init__(self, region: str = "us-east-1"):
        import boto3

        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.region = region

    def call(
        self,
        system_prompt: str,
        user_prompt: str,
        model_config: ModelConfig,
    ) -> LLMResponse:
        """Single Bedrock Converse call."""
        start = time.time()

        request_params = self._build_request(
            system_prompt, user_prompt, model_config
        )

        try:
            response = self._call_with_retry(
                lambda: self.client.converse(**request_params),
                model_config,
            )
        except Exception as e:
            return LLMResponse(
                success=False,
                error=str(e),
                model_id=model_config.model_id,
                elapsed=time.time() - start,
            )

        elapsed = time.time() - start
        content = self._extract_text(response)
        usage = response.get("usage", {})

        return LLMResponse(
            success=True,
            content=content,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            cache_write_tokens=usage.get("cacheWriteInputTokens", 0),
            cache_read_tokens=usage.get("cacheReadInputTokens", 0),
            model_id=model_config.model_id,
            elapsed=elapsed,
        )

    def run_agent_loop(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list["AgentTool"],
        model_config: ModelConfig,
        working_directory: Path,
        max_iterations: int = 50,
    ) -> AgentLoopResponse:
        """Run an agent loop: LLM calls tools until done."""
        from conductor.tools.base import ToolContext

        start = time.time()
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_write = 0
        total_cache_read = 0
        tool_calls_made = 0
        turns_since_write = 0
        files_written: list[str] = []

        tool_context = ToolContext(
            working_directory=working_directory,
        )

        # Build tool definitions for Bedrock
        tool_config = {
            "tools": [t.to_bedrock_schema() for t in tools]
        }
        tool_map = {t.name: t for t in tools}

        # Initial messages
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": user_prompt}]}
        ]

        system = [{"text": system_prompt}]

        for iteration in range(max_iterations):
            # Elapsed time check
            if time.time() - start > 900:  # 15 min
                return AgentLoopResponse(
                    completed=False,
                    final_text="Agent loop timed out (15 min)",
                    files_written=files_written,
                    tool_calls_made=tool_calls_made,
                    error="Timeout exceeded",
                )

            # Nudge if too many turns without writing
            if turns_since_write >= 30:
                messages.append({
                    "role": "user",
                    "content": [{"text": (
                        "[SYSTEM] You have made 30 tool calls without writing "
                        "any output. Please write your deliverables now."
                    )}],
                })
                turns_since_write = 0

            try:
                response = self._call_with_retry(
                    lambda: self.client.converse(
                        modelId=model_config.model_id,
                        system=system,
                        messages=messages,
                        toolConfig=tool_config,
                        inferenceConfig={
                            "temperature": model_config.temperature,
                            "maxTokens": model_config.max_output_tokens,
                        },
                    ),
                    model_config,
                )
            except Exception as e:
                return AgentLoopResponse(
                    completed=False,
                    final_text="",
                    files_written=files_written,
                    tool_calls_made=tool_calls_made,
                    error=str(e),
                )

            # Track tokens
            usage = response.get("usage", {})
            total_input_tokens += usage.get("inputTokens", 0)
            total_output_tokens += usage.get("outputTokens", 0)
            total_cache_write += usage.get("cacheWriteInputTokens", 0)
            total_cache_read += usage.get("cacheReadInputTokens", 0)

            # Check stop reason
            stop_reason = response.get("stopReason", "end_turn")
            output_message = response.get("output", {}).get("message", {})
            content_blocks = output_message.get("content", [])

            # Append assistant message
            messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason == "end_turn":
                # Done — extract final text
                final_text = ""
                for block in content_blocks:
                    if "text" in block:
                        final_text += block["text"]

                elapsed = time.time() - start
                cost = calculate_cost(
                    total_input_tokens, total_output_tokens,
                    model_config.model_id, total_cache_write, total_cache_read,
                )
                metrics = StepMetrics(
                    model_id=model_config.model_id,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    cache_write_tokens=total_cache_write,
                    cache_read_tokens=total_cache_read,
                    requests=iteration + 1,
                    elapsed_seconds=elapsed,
                    cost_usd=cost,
                )
                return AgentLoopResponse(
                    completed=True,
                    final_text=final_text,
                    files_written=files_written,
                    tool_calls_made=tool_calls_made,
                    metrics=metrics,
                )

            elif stop_reason == "tool_use":
                # Execute tool calls
                tool_results: list[dict[str, Any]] = []
                for block in content_blocks:
                    if "toolUse" in block:
                        tool_use = block["toolUse"]
                        tool_name = tool_use["name"]
                        tool_id = tool_use["toolUseId"]
                        arguments = tool_use.get("input", {})

                        tool_calls_made += 1
                        turns_since_write += 1

                        if tool_name in tool_map:
                            try:
                                # Execute tool synchronously
                                import asyncio
                                result_text = asyncio.run(
                                    tool_map[tool_name].execute(
                                        arguments, tool_context
                                    )
                                )
                                # Track writes
                                if tool_name == "write_file" and "path" in arguments:
                                    files_written.append(arguments["path"])
                                    turns_since_write = 0
                                    tool_context.files_written.append(
                                        arguments["path"]
                                    )
                            except Exception as e:
                                result_text = f"Error: {e}"
                        else:
                            result_text = f"Unknown tool: {tool_name}"

                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_id,
                                "content": [{"text": result_text}],
                            }
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                # Unknown stop reason
                break

        # Max iterations reached
        elapsed = time.time() - start
        return AgentLoopResponse(
            completed=False,
            final_text="Max iterations reached",
            files_written=files_written,
            tool_calls_made=tool_calls_made,
            error=f"Reached max iterations ({max_iterations})",
        )

    def _build_request(
        self, system_prompt: str, user_prompt: str, model_config: ModelConfig
    ) -> dict[str, Any]:
        """Build Bedrock Converse request parameters."""
        return {
            "modelId": model_config.model_id,
            "system": [{"text": system_prompt}],
            "messages": [
                {"role": "user", "content": [{"text": user_prompt}]}
            ],
            "inferenceConfig": {
                "temperature": model_config.temperature,
                "maxTokens": model_config.max_output_tokens,
            },
        }

    def _extract_text(self, response: dict) -> str:
        """Extract text content from Bedrock response."""
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        parts = []
        for block in content_blocks:
            if "text" in block:
                parts.append(block["text"])
        return "\n".join(parts)

    def _call_with_retry(self, request_fn: Any, model_config: ModelConfig) -> dict:
        """Execute a Bedrock API call with exponential backoff.

        Retries on throttling and transient errors.
        Does NOT retry on validation or access errors.
        """
        for attempt in range(1, model_config.retry_max_attempts + 1):
            try:
                return request_fn()
            except Exception as exc:
                exc_str = str(exc).lower()
                is_retryable = any(
                    k in exc_str
                    for k in (
                        "throttl",
                        "too many",
                        "rate",
                        "timeout",
                        "connection",
                        "service unavailable",
                    )
                )
                if is_retryable and attempt < model_config.retry_max_attempts:
                    wait = min(model_config.retry_base_delay ** attempt, 60)
                    time.sleep(wait)
                    continue
                raise
        # Should not reach here
        raise RuntimeError("Retry loop exhausted without result")
