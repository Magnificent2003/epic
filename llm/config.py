from __future__ import annotations
from dataclasses import dataclass
import os

from .errors import MissingAPIKeyError


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout_s: float = 60.0
    temperature: float | None = 0.2
    max_tokens: int | None = 512
    trust_env: bool = False


@dataclass(frozen=True)
class VLMSettings:
    provider: str
    api_key: str
    base_url: str
    model: str
    timeout_s: float = 60.0
    temperature: float | None = 0.2
    max_tokens: int | None = 512
    trust_env: bool = False
    request_stream: bool = False


def _get_env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip()


def _get_env_bool(name: str, default: bool) -> bool:
    v = _get_env(name)
    if v is None:
        return default
    t = v.lower()
    if t in {"1", "true", "yes", "on"}:
        return True
    if t in {"0", "false", "no", "off"}:
        return False
    return default


def _optional_float(value: str) -> float | None:
    return None if value.lower() in {"none", "null"} else float(value)


def _optional_int(value: str) -> int | None:
    return None if value.lower() in {"none", "null"} else int(value)


def load_settings() -> LLMSettings:
    """
    Load settings from environment variables.

    """
    provider = (_get_env("LLM_PROVIDER", "dashscope")).lower()

    if provider == "dashscope":
        api_key = _get_env("DASHSCOPE_API_KEY")
        base_url = _get_env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = _get_env("DASHSCOPE_MODEL", "qwen-plus")
    elif provider == "openai":
        api_key = _get_env("OPENAI_API_KEY")
        base_url = _get_env("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = _get_env("OPENAI_MODEL", "gpt-4o-mini")
    # 可以进一步扩展其他 provider
    else:
        raise ValueError(f"Unknown provider: {provider}")

    if not api_key:
        raise MissingAPIKeyError(
            f"Missing API key for provider='{provider}'. "
            f"Please set env var: {'DASHSCOPE_API_KEY' if provider=='dashscope' else 'OPENAI_API_KEY'}"
        )

    # Global settings
    timeout_s = float(_get_env("LLM_TIMEOUT_S", "60.0"))
    temperature = _optional_float(_get_env("LLM_TEMPERATURE", "0.2"))
    max_tokens = _optional_int(_get_env("LLM_MAX_TOKENS", "512"))
    trust_env = _get_env_bool("LLM_TRUST_ENV", False)

    # Return the populated settings
    return LLMSettings(
        provider=provider,
        api_key=api_key,
        base_url=base_url or "",
        model=model or "",
        timeout_s=timeout_s,
        temperature=temperature,
        max_tokens=max_tokens,
        trust_env=trust_env,
    )


def load_vlm_settings() -> VLMSettings:
    """
    Load VLM settings from environment variables.
    """
    provider = (_get_env("VLM_PROVIDER", _get_env("LLM_PROVIDER", "dashscope"))).lower()

    if provider == "dashscope":
        api_key = _get_env("DASHSCOPE_API_KEY")
        base_url = _get_env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = _get_env("DASHSCOPE_VLM_MODEL", "qwen-vl-plus")
        missing_key_name = "DASHSCOPE_API_KEY"
    elif provider == "openai":
        api_key = _get_env("OPENAI_API_KEY")
        base_url = _get_env("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = _get_env("OPENAI_VLM_MODEL", "gpt-4o-mini")
        missing_key_name = "OPENAI_API_KEY"
    elif provider == "spark":
        # Reserved for future integration.
        api_key = _get_env("SPARK_API_KEY")
        base_url = _get_env("SPARK_BASE_URL", "")
        model = _get_env("SPARK_VLM_MODEL", "")
        missing_key_name = "SPARK_API_KEY"
    else:
        raise ValueError(f"Unknown VLM provider: {provider}")

    if not api_key:
        raise MissingAPIKeyError(
            f"Missing API key for VLM provider='{provider}'. "
            f"Please set env var: {missing_key_name}"
        )

    timeout_s = float(_get_env("VLM_TIMEOUT_S", _get_env("LLM_TIMEOUT_S", "60.0")))
    temperature = _optional_float(_get_env("VLM_TEMPERATURE", _get_env("LLM_TEMPERATURE", "0.2")))
    max_tokens = _optional_int(_get_env("VLM_MAX_TOKENS", _get_env("LLM_MAX_TOKENS", "512")))
    trust_env = _get_env_bool("VLM_TRUST_ENV", _get_env_bool("LLM_TRUST_ENV", False))
    request_stream = _get_env_bool("VLM_REQUEST_STREAM", False)

    return VLMSettings(
        provider=provider,
        api_key=api_key,
        base_url=base_url or "",
        model=model or "",
        timeout_s=timeout_s,
        temperature=temperature,
        max_tokens=max_tokens,
        trust_env=trust_env,
        request_stream=request_stream,
    )
