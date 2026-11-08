from __future__ import annotations
from typing import Any, Dict, List, Optional

from llm.errors import ProviderError
from llm.providers.base import ChatMessage


class SparkVLMProvider:
    """
    Placeholder provider for future iFlytek Spark VLM integration.
    """

    def __init__(self, api_key: str, base_url: str = "", trust_env: bool = False):
        self.api_key = api_key
        self.base_url = base_url
        self.trust_env = trust_env

    def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        raise ProviderError(
            "SparkVLMProvider is reserved but not implemented yet. "
            "Please use provider='dashscope' or provider='openai' for now."
        )
