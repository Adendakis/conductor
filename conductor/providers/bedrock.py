"""AWS Bedrock Converse API provider."""

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conductor.models.metrics import StepMetrics, calculate_cost

from .base import AgentLoopResponse, LLMProvider, LLMResponse, ModelConfig

if TYPE_CHECKING:
    from conductor.tools.base import AgentTool

log = logging.getLogger("conductor.providers.bedrock")


class BedrockProvider(LLMProvider):
    """AWS Bedrock Converse API provider.

    Handles:
    - Model invocation via boto3 bedrock-runtime client
    - Exponential backoff on throttling
    - Token usage tracking per call
    """

    def __init__(self, region: str = "us-east-1"):
        import boto3
        from botocore.config import Config

        # Increase read timeout for large LLM responses (default 60s is too short
        # for generating 64K token outputs like Value_Streams.json).
        # Matches OLD_PYACM timeout: read=900s, connect=30s.
        config = Config(
            read_timeout=900,   # 15 minutes — matches old orchestrator
            connect_timeout=30,
            retries={"max_attempts": 0},  # we handle retries ourselves
        )
        self.client = boto3.client("bedrock-runtime", region_name=region, config=config)
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
        sandbox_overrides: dict | None = None,
    ) -> AgentLoopResponse:
        """Run an agent loop: LLM calls tools until done."""
        from conductor.tools.base import ToolContext, ToolSandbox

        start = time.time()
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_write = 0
        total_cache_read = 0
        tool_calls_made = 0
        turns_since_write = 0
        files_written: list[str] = []

        # Build sandbox with optional agent overrides
        sandbox_kwargs: dict[str, Any] = {"working_directory": working_directory}
        if sandbox_overrides:
            for key in (
                "read_blocked_patterns",
                "write_blocked_patterns",
                "write_allowed_exceptions",
            ):
                if key in sandbox_overrides:
                    sandbox_kwargs[key] = sandbox_overrides[key]
        sandbox = ToolSandbox(**sandbox_kwargs)

        tool_context = ToolContext(
            working_directory=working_directory,
            sandbox=sandbox,
        )

        # Build tool definitions for Bedrock
        tool_config = {
            "tools": [t.to_bedrock_schema() for t in tools]
        }
        tool_map = {t.name: t for t in tools}

        log.info(
            "🤖 Agent loop started: model=%s, tools=[%s], max_iter=%d",
            model_config.model_id,
            ", ".join(tool_map.keys()),
            max_iterations,
        )
        log.info("  Prompt: %d chars, working_dir: %s", len(user_prompt), working_directory)

        # Initial messages
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": [{"text": user_prompt}]}
        ]

        system = [{"text": system_prompt}]

        for iteration in range(max_iterations):
            # Elapsed time check
            elapsed_so_far = time.time() - start
            if elapsed_so_far > 900:  # 15 min
                log.warning("⏰ Agent loop timed out after %.1fs", elapsed_so_far)
                return AgentLoopResponse(
                    completed=False,
                    final_text="Agent loop timed out (15 min)",
                    files_written=files_written,
                    tool_calls_made=tool_calls_made,
                    error="Timeout exceeded",
                )

            log.info(
                "  ── Turn %d/%d (%.1fs elapsed, %d tool calls, tokens: in=%d out=%d)",
                iteration + 1, max_iterations, elapsed_so_far,
                tool_calls_made, total_input_tokens, total_output_tokens,
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

            # --- Sliding window history management ---
            if model_config.history_strategy != "none":
                # Use actual Bedrock token count (more accurate than char estimate)
                if total_input_tokens > model_config.history_trigger_tokens:
                    estimated_before = self._estimate_message_tokens(messages)
                    messages = self._truncate_history(
                        messages, model_config.history_keep_tokens
                    )
                    estimated_after = self._estimate_message_tokens(messages)
                    log.info(
                        "  ✂️  History truncated: actual_in=%d, est ~%d → ~%d tokens (keep=%d)",
                        total_input_tokens,
                        estimated_before,
                        estimated_after,
                        model_config.history_keep_tokens,
                    )

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
            iter_input = usage.get("inputTokens", 0)
            iter_output = usage.get("outputTokens", 0)
            total_input_tokens += iter_input
            total_output_tokens += iter_output
            total_cache_write += usage.get("cacheWriteInputTokens", 0)
            total_cache_read += usage.get("cacheReadInputTokens", 0)

            # Check stop reason
            stop_reason = response.get("stopReason", "end_turn")
            output_message = response.get("output", {}).get("message", {})
            content_blocks = output_message.get("content", [])

            log.info(
                "  ← Response: stop=%s, tokens_in=%d, tokens_out=%d, blocks=%d",
                stop_reason, iter_input, iter_output, len(content_blocks),
            )

            # Log any text reasoning from the LLM
            for block in content_blocks:
                if "text" in block:
                    text = block["text"].strip()
                    if text:
                        preview = text[:300].replace("\n", " ")
                        if len(text) > 300:
                            preview += "..."
                        log.info("  💭 %s", preview)

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
                log.info(
                    "✅ Agent loop complete: %d turns, %d tool calls, "
                    "tokens=%d/%d, %.1fs, $%.4f",
                    iteration + 1, tool_calls_made,
                    total_input_tokens, total_output_tokens,
                    elapsed, cost,
                )
                if files_written:
                    log.info("  📝 Files written: %s", files_written)
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

                        # Log tool call with argument summary
                        arg_summary = ", ".join(
                            f"{k}={str(v)[:80]!r}" for k, v in arguments.items()
                            if k != "content"  # don't log file content
                        )
                        if "content" in arguments:
                            arg_summary += f", content=<{len(arguments['content'])} chars>"
                        log.info("  🔧 Tool: %s(%s)", tool_name, arg_summary)

                        tool_start = time.time()
                        if tool_name in tool_map:
                            try:
                                # Execute tool synchronously
                                import asyncio
                                result_text = asyncio.run(
                                    tool_map[tool_name].execute(
                                        arguments, tool_context
                                    )
                                )
                                tool_elapsed = time.time() - tool_start
                                result_preview = result_text[:150].replace("\n", " ")
                                if len(result_text) > 150:
                                    result_preview += "..."
                                log.info(
                                    "    → %s (%.1fs, %d chars): %s",
                                    tool_name, tool_elapsed, len(result_text), result_preview,
                                )
                                # Track writes
                                if tool_name == "write_file" and "path" in arguments:
                                    files_written.append(arguments["path"])
                                    turns_since_write = 0
                                    tool_context.files_written.append(
                                        arguments["path"]
                                    )
                                    log.info("    📝 Wrote: %s", arguments["path"])
                            except Exception as e:
                                result_text = f"Error: {e}"
                                log.error("    ✗ Tool error: %s — %s", tool_name, e)
                        else:
                            result_text = f"Unknown tool: {tool_name}"
                            log.warning("    ⚠ Unknown tool: %s", tool_name)

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

    # ------------------------------------------------------------------
    # Sliding window history helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
        """Rough token estimate: ~4 chars per token for English text."""
        total_chars = 0
        for msg in messages:
            for block in msg.get("content", []):
                if "text" in block:
                    total_chars += len(block["text"])
                elif "toolResult" in block:
                    for sub in block["toolResult"].get("content", []):
                        if "text" in sub:
                            total_chars += len(sub["text"])
                elif "toolUse" in block:
                    # Count the input JSON roughly
                    import json
                    try:
                        total_chars += len(json.dumps(block["toolUse"].get("input", {})))
                    except (TypeError, ValueError):
                        total_chars += 100
        return total_chars // 4

    @staticmethod
    def _truncate_history(
        messages: list[dict[str, Any]], keep_tokens: int
    ) -> list[dict[str, Any]]:
        """Drop oldest messages (after the first user message) to fit budget.

        Always keeps:
        - messages[0]: the original user prompt
        - The most recent messages that fit within keep_tokens
        """
        if len(messages) <= 2:
            return messages

        first_message = messages[0]
        rest = messages[1:]

        # Walk backwards, accumulating tokens until we hit the budget
        kept: list[dict[str, Any]] = []
        running = 0
        for msg in reversed(rest):
            msg_chars = 0
            for block in msg.get("content", []):
                if "text" in block:
                    msg_chars += len(block["text"])
                elif "toolResult" in block:
                    for sub in block["toolResult"].get("content", []):
                        if "text" in sub:
                            msg_chars += len(sub["text"])
            msg_tokens = msg_chars // 4
            if running + msg_tokens > keep_tokens and kept:
                break
            running += msg_tokens
            kept.append(msg)

        kept.reverse()

        # Insert a context note so the LLM knows history was trimmed
        context_note = {
            "role": "user",
            "content": [{"text": (
                "[SYSTEM] Earlier conversation history was truncated to stay "
                "within context limits. The original task prompt and most "
                "recent messages are preserved. Do NOT re-read files you "
                "have already processed — their content was in the removed "
                "history. Continue from where you left off."
            )}],
        }

        return [first_message, context_note] + kept

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
                    log.warning(
                        "  ⏳ Retry %d/%d (wait %.1fs): %s",
                        attempt, model_config.retry_max_attempts, wait,
                        str(exc)[:100],
                    )
                    time.sleep(wait)
                    continue
                raise
        # Should not reach here
        raise RuntimeError("Retry loop exhausted without result")
