from __future__ import annotations
from typing import Any, Dict, List, Optional
from pathlib import Path
from dotenv import load_dotenv
import json
import time

from .config import LLMSettings, load_settings
from .errors import ProviderError
from .prompt import SYSTEM_PROMPT
from .providers import DashScopeQwenProvider, OpenAIChatGPTProvider
from .providers.base import ChatMessage

PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
LEGACY_ENV_PATH = Path(__file__).resolve().parent / ".env"


class LLMInvoker:
    def __init__(self, settings: LLMSettings | None = None):
        if settings is None:
            if LEGACY_ENV_PATH.exists():
                raise RuntimeError(
                    f"Found deprecated env file: {LEGACY_ENV_PATH}. "
                    f"Please keep only project root env file: {PROJECT_ENV_PATH}"
                )
            # Only load environment variables from project root `.env`.
            load_dotenv(dotenv_path=PROJECT_ENV_PATH, override=True)
            settings = load_settings()

        self.settings = settings
        if settings.provider == "dashscope":
            self.provider = DashScopeQwenProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                trust_env=settings.trust_env,
            )
        elif settings.provider == "openai":
            self.provider = OpenAIChatGPTProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                trust_env=settings.trust_env,
            )
        else:
            raise ValueError(f"Unknown provider: {settings.provider}")

    def chat(
        self,
        prompt: str,
        system: str = SYSTEM_PROMPT,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        messages: List[ChatMessage] = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=prompt),
        ]
        return self.provider.chat(
            messages=messages,
            model=self.settings.model,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            timeout_s=self.settings.timeout_s,
            extra_body=extra_body,
        )

    def chat_json(
        self,
        prompt: str,
        system: str,
        extra_body: Optional[Dict[str, Any]] = None,
        retries: int = 1,
        backoff_s: float = 0.8,
    ) -> Dict[str, Any]:
        """
        make outputs as json with prompts and parsing
        """
        last_err: Optional[Exception] = None
        for i in range(retries + 1):
            try:
                text = self.chat(prompt=prompt, system=system, extra_body=extra_body)
                return _safe_json_loads(text)
            except Exception as e:
                last_err = e
                if i < retries:
                    time.sleep(backoff_s * (2 ** i))
                else:
                    break
        raise ProviderError(f"chat_json failed after retries. last_err={last_err}") from last_err


def _safe_json_loads(text: str) -> Dict[str, Any]:
    """
    loose JSON parsing:
    1) json.loads() directly
    2) find the first { and last } and parse
    """
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    l = text.find("{")
    r = text.rfind("}")
    if l != -1 and r != -1 and r > l:
        snippet = text[l : r + 1]
        return json.loads(snippet)

    raise ValueError(f"Model output is not valid JSON: {text[:200]}...")
