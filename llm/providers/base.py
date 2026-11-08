from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union, Protocol


# OpenAI 兼容 messages 的 content 可以是 string 或多段结构（含 image_url 等）
MessageContent = Union[str, List[Dict[str, Any]]]


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: MessageContent


class ChatProvider(Protocol):
    def chat(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout_s: float,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        ...
