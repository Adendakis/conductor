"""Shared LLM helper for daily-briefing topic agents.

Uses AWS Bedrock Converse API with Claude. Each topic agent calls
`ask_llm()` with a prompt and gets back the response text.

Configure via environment variables:
  AWS_REGION (default: us-east-1)
  BRIEFING_MODEL_ID (default: anthropic.claude-sonnet-4-20250514)
"""

import os
import time
import logging

logger = logging.getLogger(__name__)

_MODEL_ID = os.environ.get("BRIEFING_MODEL_ID", "anthropic.claude-sonnet-4-20250514")
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def ask_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
    """Make a single Bedrock Converse call and return the text response.

    Retries on throttling with exponential backoff.
    Returns error message string on failure (never raises).
    """
    import boto3

    client = boto3.client("bedrock-runtime", region_name=_REGION)

    for attempt in range(1, 4):
        try:
            response = client.converse(
                modelId=_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={
                    "temperature": 0.3,
                    "maxTokens": max_tokens,
                },
            )

            # Extract text from response
            output = response.get("output", {})
            message = output.get("message", {})
            content_blocks = message.get("content", [])
            parts = [b["text"] for b in content_blocks if "text" in b]
            return "\n".join(parts)

        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("throttl", "too many", "rate")) and attempt < 3:
                wait = 2 ** attempt
                logger.warning(f"Throttled, retrying in {wait}s...")
                time.sleep(wait)
                continue
            logger.error(f"LLM call failed: {e}")
            return f"*Error: could not generate content — {e}*"

    return "*Error: max retries exceeded*"
