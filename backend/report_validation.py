"""Strict report contracts and reference checks; semantic review stays with the reviewer."""

from __future__ import annotations

import json
import re
from typing import Collection


MAX_MODEL_JSON_BYTES = 1024 * 1024
REFERENCE_PATTERN = re.compile(r"q[0-9]{2}\.[a-z][a-z0-9_]*\Z")


class ReportValidationError(ValueError):
    """Validation details contain locations and fixed messages, never answer text."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("Report validation failed")


def _error(errors: list, path: str, code: str, message: str) -> None:
    errors.append({"path": path, "code": code, "message": message})


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON member")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def parse_model_json(text: str) -> dict:
    """Require exactly one JSON object, without fenced or embedded JSON recovery."""
    try:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_MODEL_JSON_BYTES:
            raise ValueError
        result = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        # Catch overflowing finite-looking literals and escaped lone surrogates.
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise ReportValidationError([{
            "path": "", "code": "invalid_json", "message": "模型须返回有效UTF-8 JSON对象，不得包含代码围栏、额外文字或重复字段。",
        }]) from None
    if not isinstance(result, dict):
        raise ReportValidationError([{
            "path": "", "code": "type", "message": "模型输出的顶层须为JSON对象。",
        }])
    return result


def parse_model_response(text: str) -> dict:
    """Allow one complete outer JSON code fence, then parse its body strictly."""
    if isinstance(text, str):
        try:
            within_limit = len(text.encode("utf-8")) <= MAX_MODEL_JSON_BYTES
        except UnicodeError:
            within_limit = False
        if within_limit:
            fenced = re.fullmatch(
                r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```",
                text.strip(),
                flags=re.IGNORECASE,
            )
            if fenced is not None:
                text = fenced.group(1)
    return parse_model_json(text)


def _object(value: object, keys: set[str], path: str, errors: list) -> bool:
    if not isinstance(value, dict):
        _error(errors, path, "type", "该位置须为对象。")
        return False
    if set(value) - keys:
        _error(errors, path, "unknown_fields", "包含未定义的字段。")
    for key in sorted(keys - set(value)):
        _error(errors, f"{path}/{key}", "required", "缺少必要字段。")
    return True


def _text(value: object, path: str, errors: list) -> None:
    if not isinstance(value, str) or not value.strip():
        _error(errors, path, "text", "该位置须为非空文本。")


def _array(value: object, path: str, errors: list) -> bool:
    if not isinstance(value, list):
        _error(errors, path, "type", "该位置须为数组。")
        return False
    return True


def _references(value: object, path: str, allowed: set[str], minimum: int, errors: list, *, cross_question: bool = False) -> None:
    if not _array(value, path, errors):
        return
    if len(value) < minimum:
        _error(errors, path, "reference_count", "引用数量不足。")
    seen = set()
    question_ids = set()
    for index, ref in enumerate(value):
        location = f"{path}/{index}"
        if not isinstance(ref, str) or not REFERENCE_PATTERN.fullmatch(ref) or ref not in allowed:
            _error(errors, location, "unavailable_reference", "引用须对应本次转译中实际可用的子题。")
            continue
        if ref in seen:
            _error(errors, location, "duplicate_reference", "同一引用不能重复。")
        seen.add(ref)
        question_ids.add(ref.split(".", 1)[0])
    if cross_question and len(question_ids) < 2:
        _error(errors, path, "cross_question", "跨题分析须引用至少两个不同题号的可用子题。")


def _report(report: object, allowed: set[str], errors: list, prefix: str = "") -> None:
    if not _object(report, {"00", "01", "02", "03"}, prefix, errors):
        return
    if "00" in report:
        _text(report["00"], f"{prefix}/00", errors)
    comparisons = report.get("01")
    if "01" in report and _array(comparisons, f"{prefix}/01", errors):
        for index, item in enumerate(comparisons):
            path = f"{prefix}/01/{index}"
            keys = {"factor", "continue_pregnancy", "terminate_pregnancy", "evidence_refs"}
            if not _object(item, keys, path, errors):
                continue
            for key in ("factor", "continue_pregnancy", "terminate_pregnancy"):
                if key in item:
                    _text(item[key], f"{path}/{key}", errors)
            if "evidence_refs" in item:
                _references(item["evidence_refs"], f"{path}/evidence_refs", allowed, 1, errors)
    analyses = report.get("02")
    if "02" in report and _array(analyses, f"{prefix}/02", errors):
        for index, item in enumerate(analyses):
            path = f"{prefix}/02/{index}"
            if not _object(item, {"analysis", "evidence_refs"}, path, errors):
                continue
            if "analysis" in item:
                _text(item["analysis"], f"{path}/analysis", errors)
            if "evidence_refs" in item:
                _references(item["evidence_refs"], f"{path}/evidence_refs", allowed, 2, errors, cross_question=True)
    advice = report.get("03")
    if "03" in report and _object(advice, {"recommendation", "evidence_refs"}, f"{prefix}/03", errors):
        if "recommendation" in advice:
            _text(advice["recommendation"], f"{prefix}/03/recommendation", errors)
        if "evidence_refs" in advice:
            # Whether no evidence really is justified is a semantic review task.
            _references(advice["evidence_refs"], f"{prefix}/03/evidence_refs", allowed, 0, errors)


def validate_report(report: dict, allowed_refs: Collection[str]) -> None:
    errors = []
    _report(report, set(allowed_refs), errors)
    if errors:
        raise ReportValidationError(errors)


def _pointer_exists(document: object, pointer: str) -> bool:
    if pointer == "":
        return True
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        return False
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                return False
            # Avoid converting unbounded model output to an enormous integer.
            if len(token) > len(str(len(current))) or int(token) >= len(current):
                return False
            current = current[int(token)]
        else:
            return False
    return True


def validate_review(review: dict, draft: dict, allowed_refs: Collection[str]) -> None:
    errors = []
    if not _object(review, {"decision", "issues", "revised_report"}, "", errors):
        raise ReportValidationError(errors)
    decision = review.get("decision")
    if not isinstance(decision, str) or decision not in {"pass", "revise", "hold"}:
        _error(errors, "/decision", "decision", "审核结论须为pass、revise或hold。")
        decision = None
    issues = review.get("issues")
    if "issues" in review and _array(issues, "/issues", errors):
        for index, issue in enumerate(issues):
            path = f"/issues/{index}"
            if not _object(issue, {"path", "reason"}, path, errors):
                continue
            pointer = issue.get("path")
            if not isinstance(pointer, str) or not _pointer_exists(draft, pointer):
                _error(errors, f"{path}/path", "pointer", "审核位置须为指向草稿现有内容的JSON Pointer，整篇报告使用空字符串。")
            if "reason" in issue:
                _text(issue["reason"], f"{path}/reason", errors)
        if decision == "pass" and issues:
            _error(errors, "/issues", "decision_contract", "pass时问题列表须为空。")
        if decision in {"revise", "hold"} and not issues:
            _error(errors, "/issues", "decision_contract", "revise或hold时须说明需要复核或修改的理由。")
    if "revised_report" in review:
        revised = review["revised_report"]
        if decision in {"pass", "hold"} and revised is not None:
            _error(errors, "/revised_report", "decision_contract", "pass或hold时修订报告须为null。")
        elif decision == "revise":
            _report(revised, set(allowed_refs), errors, "/revised_report")
    if errors:
        raise ReportValidationError(errors)
