from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm.providers.base import ChatMessage


def chat_completion(
    client: Any,
    messages: List[ChatMessage],
    model: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
    timeout_s: float,
    request_stream: bool,
    extra_body: Optional[Dict[str, Any]],
) -> str:
    """Return one complete answer, regardless of the transport format."""
    if extra_body is not None and "stream" in extra_body:
        raise ValueError(
            "extra_body cannot override stream; configure request_stream instead "
            "(VLM_REQUEST_STREAM for VLM calls)."
        )
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "timeout": timeout_s,
        "stream": request_stream,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if extra_body:
        kwargs["extra_body"] = extra_body

    response = client.chat.completions.create(**kwargs)
    if not request_stream:
        if isinstance(response, (str, bytes)) or not hasattr(response, "choices"):
            raise ValueError(
                "Expected a JSON chat completion, but the endpoint returned another "
                "response format (possibly SSE). For VLM calls, set "
                "VLM_REQUEST_STREAM=1 to collect the stream into a complete answer."
            )
        if not response.choices:
            raise ValueError("The chat completion response contains no choices.")
        return response.choices[0].message.content or ""

    parts: List[str] = []
    try:
        for chunk in response:
            if not hasattr(chunk, "choices"):
                raise ValueError("Expected an SSE chat completion chunk with choices.")
            if not chunk.choices:
                # Some endpoints send a final chunk containing only usage statistics.
                continue
            for choice in chunk.choices:
                # Match non-streaming calls, which return only the first candidate.
                if choice.index != 0:
                    continue
                content = choice.delta.content
                if content:
                    parts.append(content)
        return "".join(parts)
    finally:
        # Release the connection even if iteration raises; never return partial text.
        close = getattr(response, "close", None)
        if callable(close):
            close()
