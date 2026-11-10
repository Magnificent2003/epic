"""Validate questionnaire answers without a web framework or model inference."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Any


STATUSES = {"answered", "unanswered", "declined", "not_applicable", "skipped"}
RELATIONS = {
    "favors_continue", "favors_terminate", "supports_both", "conflict", "uncertain"
}


class ValidationError(ValueError):
    """Structured errors deliberately omit submitted answer values."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("Questionnaire validation failed")


def _error(errors: list, field: str, code: str, message: str) -> None:
    errors.append({"field": field, "code": code, "message": message})


def field_index(definition: dict) -> dict[str, dict]:
    """Return fields in display order; reject an invalid local definition."""
    index: dict[str, dict] = {}
    question_ids: set[str] = set()
    for question in definition["questions"]:
        qid = question["id"]
        if qid in question_ids:
            raise ValueError("Duplicate questionnaire question ID")
        question_ids.add(qid)
        for field in question["fields"]:
            ref = f"{qid}.{field['id']}"
            if ref in index:
                raise ValueError("Duplicate questionnaire field ID")
            index[ref] = field
    for field in index.values():
        rule = field.get("visible_when")
        if rule and rule["field"] not in index:
            raise ValueError("Visibility dependency does not exist")
    return index


def evaluate_visibility(rule: dict | None, answers: dict) -> bool | None:
    """Three-state rule evaluation: None means the source is not yet known.

    A skipped or explicitly inapplicable source makes its dependent field
    inapplicable too. Unanswered/declined sources never imply an answer.
    """
    if rule is None:
        return True
    qid, fid = rule["field"].split(".", 1)
    source = answers.get(qid, {}).get(fid, {"status": "unanswered", "value": None})
    status = source["status"]
    if status in {"skipped", "not_applicable"}:
        return False
    if status != "answered":
        return None
    actual, expected = source["value"], rule["value"]
    op = rule["operator"]
    if op == "equals":
        return actual == expected
    if op == "not_equals":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "contains":
        return expected in actual
    raise ValueError("Unsupported visibility operator")


def _check_value(value: Any, field: dict, ref: str, index: dict, errors: list) -> None:
    kind = field["type"]
    if kind == "integer":
        if type(value) is not int:
            _error(errors, ref, "type", "请填写整数。")
        elif ("minimum" in field and value < field["minimum"]) or (
            "maximum" in field and value > field["maximum"]
        ):
            _error(errors, ref, "range", "数值超出该字段允许的范围。")
        return
    if kind in {"date", "year_month"}:
        pattern = r"\d{4}-\d{2}-\d{2}" if kind == "date" else r"\d{4}-\d{2}"
        try:
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError
            date.fromisoformat(value if kind == "date" else value + "-01")
        except ValueError:
            _error(errors, ref, "date", "请使用有效的日期或年月格式。")
        return
    if kind == "text":
        if not isinstance(value, str) or not value.strip():
            _error(errors, ref, "type", "已作答的文本不能为空。")
        elif len(value) > field.get("max_length", 4000):
            _error(errors, ref, "max_length", "文本超过允许长度。")
        return
    if kind == "single_select":
        codes = {option["code"] for option in field["options"]}
        if not isinstance(value, str) or value not in codes:
            _error(errors, ref, "option", "请选择该字段提供的选项。")
        return
    if kind in {"multi_select", "field_refs"}:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            _error(errors, ref, "type", "请提交字符串数组。")
            return
        if len(set(value)) != len(value):
            _error(errors, ref, "duplicate", "不能重复选择同一项。")
        if kind == "multi_select":
            if not value:
                _error(errors, ref, "empty_selection", "未选择时应记录为未答，不能记录为空的已作答多选。")
            codes = {option["code"] for option in field["options"]}
            if any(item not in codes for item in value):
                _error(errors, ref, "option", "包含该字段未提供的选项。")
            if len(value) > 1 and set(value).intersection(field.get("exclusive_options", [])):
                _error(errors, ref, "exclusive", "排他选项不能与其他选项同时选择。")
            for conflict in field.get("conflicts", []):
                if set(conflict).issubset(value):
                    _error(errors, ref, "conflict", "所选项目不能同时选择。")
        else:
            business_refs = {
                item for item in index
                if item.split(".", 1)[0] not in {"consent", "q38"} or item == "q38.report_notes"
            }
            allowed_refs = set(field.get("allowed_refs", business_refs)) & business_refs
            if any(item not in allowed_refs for item in value):
                _error(errors, ref, "field_reference", "只能选择定义中允许排除的业务子字段。")
        if len(value) > field.get("max_selections", len(index)):
            _error(errors, ref, "max_selections", "选择数量超过允许上限。")
        return
    if kind == "ranked_values":
        if not isinstance(value, list) or not value:
            _error(errors, ref, "type", "请提交至少一条价值考虑，未填写时记录为未答。")
            return
        if len(value) > field.get("max_selections", 3):
            _error(errors, ref, "max_selections", "价值考虑最多填写规定数量。")
        factors: set[str] = set()
        factor_limit = field.get("factor_max_length", 500)
        conditions_limit = field.get("conditions_max_length", 2000)
        relation_field = next((item for item in field.get("item_fields", []) if item["id"] == "relation"), None)
        relation_options = relation_field.get("options") if relation_field is not None else None
        relations = {option["code"] for option in relation_options} if relation_options is not None else RELATIONS
        for item in value:
            if not isinstance(item, dict) or set(item) != {"factor", "relation", "conditions"}:
                _error(errors, ref, "ranked_item", "每条考虑须包含考虑因素、选择关系和条件。")
                continue
            factor = item["factor"]
            if not isinstance(factor, str) or not factor.strip() or len(factor) > factor_limit:
                _error(errors, ref, "ranked_factor", "考虑因素须为非空文本，且不能超过规定长度。")
            elif factor.strip() in factors:
                _error(errors, ref, "duplicate", "请勿重复填写同一考虑因素。")
            else:
                factors.add(factor.strip())
            if not isinstance(item["relation"], str) or item["relation"] not in relations:
                _error(errors, ref, "ranked_relation", "请选择有效的选择关系。")
            if not isinstance(item["conditions"], str) or len(item["conditions"]) > conditions_limit:
                _error(errors, ref, "ranked_conditions", "条件须为规定长度内的文本，可以留空。")
        return
    raise ValueError("Unsupported questionnaire field type")


def normalize_submission(payload: Any, definition: dict) -> dict:
    """Validate and normalize a draft, without storing it or requiring consent.

    Valid answers to newly hidden fields are discarded. All values are checked
    before visibility is applied, so hidden fields cannot conceal invalid data.
    Submission authorization is enforced separately by submit_action().
    """
    errors: list[dict[str, str]] = []
    index = field_index(definition)
    expected = {
        "schema_version": definition["schema_version"],
        "questionnaire_id": definition["questionnaire_id"],
        "questionnaire_version": definition["version"],
    }
    if not isinstance(payload, dict):
        raise ValidationError([{"field": "$", "code": "type", "message": "请求须为JSON对象。"}])
    if set(payload) - {*expected, "answers"}:
        _error(errors, "$", "unknown_field", "请求包含未定义的顶层字段。")
    for key, value in expected.items():
        if payload.get(key) != value:
            _error(errors, key, "version", "问卷标识或版本不匹配。")
    submitted = payload.get("answers")
    if not isinstance(submitted, dict):
        _error(errors, "answers", "type", "answers须为对象。")
        raise ValidationError(errors)
    known_questions = {question["id"]: question for question in definition["questions"]}
    for qid, fields in submitted.items():
        if qid not in known_questions:
            _error(errors, "answers", "unknown_question", "包含未定义的题目。")
            continue
        if not isinstance(fields, dict):
            _error(errors, qid, "type", "题目答案须为子字段对象。")
            continue
        known_fields = {field["id"] for field in known_questions[qid]["fields"]}
        if set(fields) - known_fields:
            _error(errors, qid, "unknown_field", "包含未定义的子字段。")
    answers: dict[str, dict] = {qid: {} for qid in known_questions}
    for ref, field in index.items():
        qid, fid = ref.split(".", 1)
        source = submitted.get(qid, {})
        answer = source.get(fid, {"status": "unanswered", "value": None}) if isinstance(source, dict) else None
        if not isinstance(answer, dict) or not {"status", "value"}.issubset(answer):
            _error(errors, ref, "answer_shape", "答案须包含status和value。")
            continue
        if set(answer) - {"status", "value", "skip_rule_id"}:
            _error(errors, ref, "unknown_field", "答案包含未定义的属性。")
        status, value = answer["status"], answer["value"]
        if not isinstance(status, str) or status not in STATUSES:
            _error(errors, ref, "status", "回答状态无效。")
            continue
        if status == "answered":
            _check_value(value, field, ref, index, errors)
        elif value is not None:
            _error(errors, ref, "null_required", "非已作答状态的value须为null。")
        if status == "not_applicable" and not field.get("allow_not_applicable", False):
            _error(errors, ref, "not_applicable", "该字段不提供自行选择不适用的操作。")
        if status == "skipped":
            if not isinstance(answer.get("skip_rule_id"), str) or not answer["skip_rule_id"]:
                _error(errors, ref, "skip_rule", "规则跳过须提供规则标识。")
        elif "skip_rule_id" in answer:
            _error(errors, ref, "skip_rule", "只有规则跳过状态可以包含规则标识。")
        answers[qid][fid] = deepcopy(answer)
    if errors:
        raise ValidationError(errors)

    visiting: set[str] = set()
    resolved: set[str] = set()

    def resolve(ref: str) -> None:
        if ref in resolved:
            return
        if ref in visiting:
            raise ValueError("Cyclic questionnaire visibility dependency")
        visiting.add(ref)
        field = index[ref]
        rule = field.get("visible_when")
        if rule:
            resolve(rule["field"])
        qid, fid = ref.split(".", 1)
        answer = answers[qid][fid]
        visible = evaluate_visibility(rule, answers)
        if visible is False:
            if answer["status"] == "skipped" and answer["skip_rule_id"] != rule["rule_id"]:
                _error(errors, ref, "skip_rule", "跳题规则标识不匹配。")
            answers[qid][fid] = {"status": "skipped", "value": None, "skip_rule_id": rule["rule_id"]}
        elif answer["status"] == "skipped":
            _error(errors, ref, "invalid_skip", "当前条件不允许跳过该字段。")
        visiting.remove(ref)
        resolved.add(ref)

    for ref in index:
        resolve(ref)
    if errors:
        raise ValidationError(errors)
    return {**expected, "answers": answers}


def submit_action(normalized: dict, definition: dict) -> str:
    """Enforce flow gates after normalization; only 'submit' may be stored."""
    answers = normalized["answers"]

    def value(qid: str, fid: str) -> Any:
        answer = answers.get(qid, {}).get(fid, {})
        return answer.get("value") if answer.get("status") == "answered" else None

    consent = value("consent", "report_use")
    report_action = value("q38", "report_action")
    if consent == "disagree" or report_action == "stop":
        return "stop"
    if consent != "agree":
        raise ValidationError([{"field": "consent.report_use", "code": "consent_required", "message": "正式提交前须明确同意本次报告用途。"}])
    privacy = value("consent", "private_completion")
    if privacy == "uncomfortable":
        return "pause"
    if privacy not in {"comfortable", "assisted"}:
        raise ValidationError([{"field": "consent.private_completion", "code": "required", "message": "请先确认目前的填写环境。"}])
    if report_action == "edit":
        return "edit"
    if report_action != "confirm":
        raise ValidationError([{"field": "q38.report_action", "code": "confirmation_required", "message": "正式提交前须核对并确认本次完整答卷。"}])
    errors: list[dict[str, str]] = []
    for ref, field in field_index(definition).items():
        qid, fid = ref.split(".", 1)
        answer = answers[qid][fid]
        if field.get("required") and answer["status"] not in {"answered", "skipped"}:
            _error(errors, ref, "required", "请完成此项必要确认。")
    if errors:
        raise ValidationError(errors)
    return "submit"


def eligible_analysis_answers(normalized: dict, definition: dict) -> dict:
    """Prepare a future model input; this function does not call a model.

    The normalized answer set must pass the same authorization and final
    confirmation gates as a formal submission. Drafts and stop/edit/pause
    actions never produce an analysis input.
    Exclusion notes need explicit resolution before any report pipeline may
    proceed. Structured exclusions also remove their dependent supplement
    fields to avoid leaking the same content through a conditional note.
    """
    if submit_action(normalized, definition) != "submit":
        raise ValidationError([{"field": "q38.report_action", "code": "analysis_not_authorized", "message": "当前答卷未确认进入分析，不能准备模型输入。"}])
    answers = normalized["answers"]
    q38 = answers.get("q38", {})
    notes = q38.get("exclusion_notes", {})
    # An exact standalone "无" explicitly says there is no free-text exclusion.
    # Never match substrings: "无其他要求，但请隐藏年龄" still needs resolution.
    exclusion_text = notes.get("value", "").strip() if notes.get("status") == "answered" else ""
    if exclusion_text not in {"", "无"}:
        raise ValidationError([{"field": "q38.exclusion_notes", "code": "unresolved_exclusion", "message": "请先将文字隐藏要求核对为明确字段，再进入报告生成。"}])
    excluded_answer = q38.get("excluded_report_fields", {})
    excluded = set(excluded_answer.get("value") or []) if excluded_answer.get("status") == "answered" else set()
    index = field_index(definition)
    changed = True
    while changed:
        changed = False
        for ref, field in index.items():
            if ref not in excluded and field.get("visible_when", {}).get("field") in excluded:
                excluded.add(ref)
                changed = True
    result: dict[str, dict] = {}
    for qid, fields in answers.items():
        if qid == "consent":
            continue
        selected = {
            fid: deepcopy(answer) for fid, answer in fields.items()
            if f"{qid}.{fid}" not in excluded and (qid != "q38" or fid == "report_notes")
        }
        if selected:
            result[qid] = selected
    return result
