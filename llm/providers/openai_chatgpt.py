from __future__ import annotations
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI

from llm.providers.base import ChatMessage
from llm.providers._chat_completion import chat_completion
from llm.errors import ProviderError


class OpenAIChatGPTProvider:
    """
    OpenAI: chat.completions
    defalt_base_url: https://api.openai.com/v1
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        trust_env: bool = False,
        request_stream: bool = False,
    ):
        self.request_stream = request_stream
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            # Avoid accidentally using broken system proxy vars unless explicitly enabled.
            http_client=httpx.Client(trust_env=trust_env),
        )

    def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout_s: float,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        try:
            return chat_completion(
                client=self.client,
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                request_stream=self.request_stream,
                extra_body=extra_body,
            )
        except Exception as e:
            hint = ""
            msg = str(e)
            if "Connection refused" in msg or "ConnectError" in msg:
                hint = " Hint: check proxy env vars (HTTP_PROXY/HTTPS_PROXY) or set LLM_TRUST_ENV=0."
            raise ProviderError(f"OpenAIChatGPTProvider call failed: {e}.{hint}") from e
