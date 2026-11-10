"""Generate and independently review one authorized questionnaire revision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Protocol
from uuid import uuid4

from .report_validation import (
    ReportValidationError, parse_model_json, parse_model_response, validate_report, validate_review,
)
from .translation import model_input_text, translate_submission
from .validation import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILES = ("generate_report.md", "review_report.md", "report_schema.json", "review_schema.json")


class PipelineError(ValueError):
    """Safe operational information; never include answers or provider errors."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class ReportClient(Protocol):
    def chat(self, prompt: str, system: str) -> str: ...


@dataclass(frozen=True)
class PreparedCase:
    input_path: Path
    source_hashes: dict[Path, str]
    case_input: str
    allowed_refs: frozenset[str]

    def check_unchanged(self) -> None:
        try:
            unchanged = all(_hash(path.read_bytes()) == digest for path, digest in self.source_hashes.items())
        except OSError:
            unchanged = False
        if not unchanged:
            raise PipelineError("input_changed", "答卷、转译文件或问卷定义已变化；请重新转译后运行。")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_object(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        value = parse_model_json(raw.decode("utf-8"))
    except OSError:
        raise PipelineError("input_unavailable", "无法读取所需文件，请检查输入路径、同目录答卷和问卷定义。") from None
    except (UnicodeError, ReportValidationError):
        raise PipelineError("invalid_input_json", "输入文件须为有效的JSON对象。") from None
    return value, _hash(raw)


def prepare_case(model_input_path: str | Path, definition_path: str | Path | None = None) -> PreparedCase:
    """Recheck authorization and freshness locally; raw answers never enter a prompt."""
    input_path = Path(model_input_path).resolve()
    if input_path.name != "model_input.json":
        raise PipelineError("input_filename", "请传入需要处理的 model_input.json。")
    answers_path = input_path.with_name("answers.json")
    definition_path = Path(definition_path).resolve() if definition_path else PROJECT_ROOT / "questionnaire/definition.json"
    translation, translation_hash = _read_object(input_path)
    record, record_hash = _read_object(answers_path)
    definition, definition_hash = _read_object(definition_path)
    try:
        fresh_translation = translate_submission(record, definition)
    except ValidationError:
        raise PipelineError("invalid_answers", "原始答卷未通过校验，请先检查答卷并重新转译。") from None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PipelineError("invalid_definition", "问卷定义无效，无法核对本次转译。") from None
    if fresh_translation["status"] != "ready":
        raise PipelineError("input_blocked", "本次答卷尚未获准分析，或文字排除要求仍待明确；不能生成报告。")
    if translation != fresh_translation:
        raise PipelineError("stale_translation", "转译文件与当前答卷或规则不一致，请先重新转译。")
    refs = set()
    for line in translation["lines"]:
        match = re.match(r"^\[(q\d{2}\.[a-z][a-z0-9_]*)\] ", line)
        if match is None or match.group(1) in refs:
            raise PipelineError("invalid_field_reference", "转译记录须使用唯一的 qNN.field 字段标识。")
        refs.add(match.group(1))
    prepared = PreparedCase(
        input_path=input_path,
        source_hashes={input_path: translation_hash, answers_path: record_hash, definition_path: definition_hash},
        case_input=model_input_text(translation),
        allowed_refs=frozenset(refs),
    )
    prepared.check_unchanged()
    return prepared


def load_prompts(prompt_dir: str | Path | None = None) -> tuple[dict, dict]:
    directory = Path(prompt_dir) if prompt_dir else PROJECT_ROOT / "prompt"
    texts, hashes = {}, {}
    try:
        for name in PROMPT_FILES:
            raw = (directory / name).read_bytes()
            text = raw.decode("utf-8")
            if not text.strip():
                raise ValueError("Empty prompt")
            texts[name] = parse_model_json(text) if name.endswith(".json") else text
            hashes[name] = _hash(raw)
    except (OSError, UnicodeError, ValueError):
        raise PipelineError("invalid_prompts", "提示词或输出格式定义缺失或无效。") from None
    return texts, hashes


def _default_client() -> ReportClient:
    # Import lazily so questionnaire serving and offline tests need no SDK.
    try:
        from llm import LLMInvoker
    except ImportError:
        raise PipelineError("missing_dependencies", "请先安装 requirements.txt 中的模型调用依赖。") from None
    try:
        client = LLMInvoker()
    except Exception:
        raise PipelineError("model_configuration", "模型初始化失败，请检查根目录 .env 中的 DashScope 配置。") from None
    if client.settings.provider != "dashscope":
        raise PipelineError("provider_mismatch", "本链路使用 DashScope，请在根目录 .env 设置 LLM_PROVIDER=DashScope。")
    return client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".report-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _call(client: ReportClient, system: str, payload: dict) -> dict:
    try:
        text = client.chat(prompt=json.dumps(payload, ensure_ascii=False), system=system)
    except Exception:
        # Providers may embed request bodies, answers or credentials in errors.
        raise PipelineError("model_call_failed", "模型调用失败，未发布最终报告；可检查模型配置或网络后重新运行。") from None
    return parse_model_response(text)


def run_report(
    model_input_path: str | Path,
    *,
    definition_path: str | Path | None = None,
    prompt_dir: str | Path | None = None,
    client: ReportClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run two independent calls, persisting every attempt in its own directory.

    Preflight errors raise PipelineError before any model initialization/call.
    Once a run exists, failures are recorded and returned without a final report.
    Only run.json with status='approved' identifies a usable final_report.json.
    There are no automatic generation/review retries or automatic hold overrides.
    """
    case = prepare_case(model_input_path, definition_path)
    prompts, prompt_hashes = load_prompts(prompt_dir)
    run_id = str(uuid4())
    directory = case.input_path.parent / "reports" / run_id
    try:
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        directory.mkdir(mode=0o700)
    except OSError:
        raise PipelineError("output_unavailable", "无法创建报告运行目录，请检查本地写入权限。") from None
    state = {
        "pipeline_version": "1.0", "run_id": run_id,
        "status": "running", "stage": "generate",
        "started_at": _now(), "finished_at": None,
        "source_sha256": {path.name: digest for path, digest in case.source_hashes.items()},
        "prompt_sha256": prompt_hashes,
    }
    state_path = directory / "run.json"
    final_path = directory / "final_report.json"
    try:
        _write_json(state_path, state)
        if client is None:
            client = _default_client()
            state["model"] = {"provider": client.settings.provider, "name": client.settings.model}
            _write_json(state_path, state)

        case.check_unchanged()
        if progress:
            progress("generating")
        draft = _call(client, prompts["generate_report.md"], {
            "case_input": case.case_input, "report_schema": prompts["report_schema.json"],
        })
        case.check_unchanged()
        validate_report(draft, case.allowed_refs)
        _write_json(directory / "draft_report.json", draft)

        state["stage"] = "review"
        _write_json(state_path, state)
        if progress:
            progress("reviewing")
        case.check_unchanged()
        review = _call(client, prompts["review_report.md"], {
            "draft_report": draft, "case_input": case.case_input,
            "report_schema": prompts["report_schema.json"], "review_schema": prompts["review_schema.json"],
        })
        case.check_unchanged()
        validate_review(review, draft, case.allowed_refs)
        _write_json(directory / "review_report.json", review)

        state["stage"] = "finalize"
        state["decision"] = review["decision"]
        if review["decision"] == "hold":
            state["status"] = "hold"
        else:
            final_report = draft if review["decision"] == "pass" else review["revised_report"]
            validate_report(final_report, case.allowed_refs)
            case.check_unchanged()
            _write_json(final_path, final_report)
            case.check_unchanged()
            state["status"] = "approved"
        state["stage"] = "complete"
        state["finished_at"] = _now()
        _write_json(state_path, state)
    except (Exception, KeyboardInterrupt) as exc:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            # The failed run state remains authoritative even if cleanup is
            # prevented by an external permission change.
            pass
        state["status"] = "failed"
        state["finished_at"] = _now()
        if isinstance(exc, PipelineError):
            error = {"code": exc.code, "message": exc.message}
        elif isinstance(exc, ReportValidationError):
            error = {"code": "invalid_model_output", "message": "模型输出的JSON结构、审核处置或证据引用未通过校验。", "issues": exc.errors}
        elif isinstance(exc, KeyboardInterrupt):
            error = {"code": "interrupted", "message": "本次运行已中断，未发布最终报告。"}
        elif isinstance(exc, OSError):
            error = {"code": "write_failed", "message": "报告文件写入失败，未发布最终报告。"}
        else:
            error = {"code": "pipeline_failed", "message": "报告处理失败，未发布最终报告。"}
        state["error"] = error
        try:
            _write_json(state_path, state)
        except OSError:
            raise PipelineError("write_failed", "报告运行状态无法保存，本次结果不可使用。") from None
    return {**state, "run_dir": str(directory)}
