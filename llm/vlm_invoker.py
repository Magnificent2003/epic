from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union
from dotenv import load_dotenv
import base64
import json
import mimetypes
import time

from .config import VLMSettings, load_vlm_settings
from .errors import ProviderError
from .prompt import SYSTEM_PROMPT
from .providers import DashScopeQwenProvider, OpenAIChatGPTProvider, SparkVLMProvider
from .providers.base import ChatMessage

PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
LEGACY_ENV_PATH = Path(__file__).resolve().parent / ".env"

ImageInput = Union[str, Path]


class _Unset:
    """Distinguish omitted arguments from explicitly supplied aliases."""


_UNSET = _Unset()
PromptArgument = Union[str, None, _Unset]
ImageArgument = Union[ImageInput, Sequence[ImageInput], None, _Unset]


class VLMInvoker:
    def __init__(self, settings: VLMSettings | None = None):
        if settings is None:
            if LEGACY_ENV_PATH.exists():
                raise RuntimeError(
                    f"Found deprecated env file: {LEGACY_ENV_PATH}. "
                    f"Please keep only project root env file: {PROJECT_ENV_PATH}"
                )
            load_dotenv(dotenv_path=PROJECT_ENV_PATH, override=True)
            settings = load_vlm_settings()

        self.settings = settings
        if settings.provider == "dashscope":
            self.provider = DashScopeQwenProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                trust_env=settings.trust_env,
                request_stream=settings.request_stream,
            )
        elif settings.provider == "openai":
            self.provider = OpenAIChatGPTProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                trust_env=settings.trust_env,
                request_stream=settings.request_stream,
            )
        elif settings.provider == "spark":
            self.provider = SparkVLMProvider(
                api_key=settings.api_key,
                base_url=settings.base_url,
                trust_env=settings.trust_env,
            )
        else:
            raise ValueError(f"Unknown VLM provider: {settings.provider}")

    def chat(
        self,
        prompt: PromptArgument = _UNSET,
        images: ImageArgument = _UNSET,
        system: PromptArgument = _UNSET,
        extra_body: Optional[Dict[str, Any]] = None,
        *,
        system_prompt: PromptArgument = _UNSET,
        user_prompt: PromptArgument = _UNSET,
        image_paths: ImageArgument = _UNSET,
        label_images: bool = False,
    ) -> str:
        """Return complete text for one call, accepting both new and legacy names.

        Omitted/None system prompts use SYSTEM_PROMPT; an empty string omits
        the system message. Empty user prompts leave the images intact.
        """
        messages = _build_messages(
            prompt, images, system,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=image_paths,
            label_images=label_images,
        )
        return self._chat(messages, extra_body)

    def _chat(
        self, messages: List[ChatMessage], extra_body: Optional[Dict[str, Any]]
    ) -> str:
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
        prompt: PromptArgument = _UNSET,
        images: ImageArgument = _UNSET,
        system: PromptArgument = _UNSET,
        extra_body: Optional[Dict[str, Any]] = None,
        retries: int = 1,
        backoff_s: float = 0.8,
        *,
        system_prompt: PromptArgument = _UNSET,
        user_prompt: PromptArgument = _UNSET,
        image_paths: ImageArgument = _UNSET,
        label_images: bool = False,
    ) -> Dict[str, Any]:
        """Call the model and parse JSON, retrying calls or parsing failures."""
        if retries < 0 or backoff_s < 0:
            raise ValueError("retries and backoff_s must be non-negative.")
        # Validate and encode once, before any model call or retry.
        messages = _build_messages(
            prompt, images, system,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_paths=image_paths,
            label_images=label_images,
        )
        last_err: Optional[Exception] = None
        for i in range(retries + 1):
            try:
                text = self._chat(messages, extra_body)
                return _safe_json_loads(text)
            except Exception as e:
                last_err = e
                if i < retries:
                    time.sleep(backoff_s * (2 ** i))
                else:
                    break
        raise ProviderError(f"vlm chat_json failed after retries. last_err={last_err}") from last_err


def _resolve_alias(legacy: Any, preferred: Any, legacy_name: str, preferred_name: str, default: Any) -> Any:
    if legacy is not _UNSET and preferred is not _UNSET:
        raise TypeError(f"Use either `{legacy_name}` or `{preferred_name}`, not both.")
    value = preferred if preferred is not _UNSET else legacy
    return default if value is _UNSET or value is None else value


def _build_messages(
    prompt: PromptArgument,
    images: ImageArgument,
    system: PromptArgument,
    *,
    system_prompt: PromptArgument,
    user_prompt: PromptArgument,
    image_paths: ImageArgument,
    label_images: bool,
) -> List[ChatMessage]:
    user_text = _resolve_alias(prompt, user_prompt, "prompt", "user_prompt", "")
    system_text = _resolve_alias(system, system_prompt, "system", "system_prompt", SYSTEM_PROMPT)
    image_inputs = _resolve_alias(images, image_paths, "images", "image_paths", None)
    if not isinstance(user_text, str) or not isinstance(system_text, str):
        raise TypeError("System and user prompts must be strings or None.")

    image_urls = _normalize_images(image_inputs)
    if not user_text.strip() and not image_urls:
        raise ValueError("Provide a user prompt or at least one image.")

    user_content: List[Dict[str, Any]] = []
    if user_text.strip():
        user_content.append({"type": "text", "text": user_text})
    for index, image_url in enumerate(image_urls, start=1):
        if label_images:
            user_content.append({"type": "text", "text": f"第 {index} 张图片："})
        user_content.append({"type": "image_url", "image_url": {"url": image_url}})

    messages: List[ChatMessage] = []
    if system_text.strip():
        messages.append(ChatMessage(role="system", content=system_text))
    messages.append(ChatMessage(role="user", content=user_content))
    return messages


def _normalize_images(images: ImageInput | Sequence[ImageInput] | None) -> List[str]:
    if images is None:
        return []
    candidates: List[ImageInput]
    if isinstance(images, (str, Path)):
        candidates = [images]
    else:
        candidates = list(images)

    return [_normalize_single_image(item) for item in candidates]


def _normalize_single_image(image: ImageInput) -> str:
    if not isinstance(image, (str, Path)):
        raise TypeError("Image inputs must be strings or pathlib.Path objects.")
    raw = str(image).strip()
    if not raw:
        raise ValueError("Image input cannot be empty.")

    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("data:"):
        return raw

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Image path is not a file: {path}")

    mime_type, _ = mimetypes.guess_type(path.name)
    if not mime_type:
        mime_type = "application/octet-stream"

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _safe_json_loads(text: str) -> Dict[str, Any]:
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
