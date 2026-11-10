"""Offline, full-fidelity answer sheets for comparing questionnaires and reports."""

from __future__ import annotations

from datetime import datetime
from html import escape
from io import BytesIO
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from .report_rendering import _pdf_font
from .report_validation import parse_model_json
from .validation import field_index, normalize_submission


OUTPUT_FILENAME = "questionnaire.pdf"
STATUS_LABELS = {
    "answered": "已作答", "unanswered": "未填写", "declined": "不愿回答",
    "not_applicable": "不适用", "skipped": "按条件跳过",
}
TYPE_LABELS = {
    "single_select": "单选", "multi_select": "多选", "integer": "整数",
    "text": "文字补充", "date": "日期，YYYY-MM-DD", "year_month": "年月，YYYY-MM",
    "ranked_values": "按重要程度排序", "field_refs": "按题目或子题选择",
}


def _submission_time(value: str | None) -> str:
    if not value:
        return "未记录"
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is not None:
            return instant.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S") + "（北京时间，UTC+08:00）"
    except ValueError:
        pass
    return value + "（原始记录，时区或格式未确认）"


def questionnaire_blocks(record: dict, definition: dict) -> list[tuple[str, str]]:
    """Return the original wording, all choices and exact recorded answer states.

    This export deliberately does not use model_input or report authorization
    filters: excluded/declined/skipped fields belong in the developer's record.
    Reject inconsistent stored records rather than silently normalizing them.
    """
    payload = {key: record[key] for key in ("schema_version", "questionnaire_id", "questionnaire_version", "answers")}
    normalized = normalize_submission(payload, definition)
    if normalized["answers"] != record["answers"]:
        raise ValueError("原始答卷字段或跳题状态不完整、不一致；请先核对，导出不会自动改写答案。")
    index = field_index(definition)
    questions = {question["id"]: question for question in definition["questions"]}
    blocks: list[tuple[str, str]] = []

    def add(text, style="body"):
        if text is not None and str(text):
            blocks.append((style, str(text)))

    def ref_label(ref):
        question = questions[ref.split(".", 1)[0]]
        prefix = f"第{question['source_number']}题" if question.get("source_number") is not None else "填写前确认"
        return f"{prefix} · {index[ref]['label']}"

    def options(field, selected):
        for option in field["options"]:
            add(f"{'[x]' if option['code'] in selected else '[ ]'} {option['label']}", "option")

    def condition(rule):
        source = index[rule["field"]]
        labels = {option["code"]: option["label"] for option in source.get("options", [])}
        values = rule["value"] if isinstance(rule["value"], list) else [rule["value"]]
        expected = "、".join(f"“{labels.get(value, value)}”" for value in values)
        operations = {"equals": "为", "not_equals": "不为", "contains": "包含", "in": "为以下任一项", "not_in": "不为以下任一项"}
        return f"显示条件：{ref_label(rule['field'])}{operations[rule['operator']]}{expected}时填写；依赖题按规则跳过或不适用时也跳过。"

    add(definition["title"], "title")
    add("原始答卷 · 开发者对照版", "heading")
    add(f"答题时间（提交时间）：{_submission_time(record.get('submitted_at'))}")
    if record.get("submitted_at"):
        add(f"原始时间记录：{record['submitted_at']}", "small")
    add(f"答卷编号：{record.get('submission_id', '未记录')}　版本：{record.get('revision', '未记录')}")
    add(f"问卷版本：{record['questionnaire_version']}")
    add("现有答卷只记录提交时间，未记录开始时间或逐题作答时间。", "small")
    add("[x] 表示已勾选，[ ] 表示未勾选；未勾选不代表否定。保留全部题目、原始说明及跳过题，状态以各字段记录为准。", "small")
    add("此文件直接呈现 answers.json，包括报告中排除的回答，用于开发者核对；不调用模型，不改写回答。", "small")
    add(definition.get("intro"))
    for instruction in definition.get("instructions", []):
        add(instruction)

    for section in definition["sections"]:
        add(section["title"], "section")
        add(section.get("intro"))
        for qid in section["question_ids"]:
            question = questions[qid]
            number = question.get("source_number")
            add(f"{'确认' if number is None else str(number)}　{question['title']}", "heading")
            add(question.get("intro"))
            for field in question["fields"]:
                ref = f"{qid}.{field['id']}"
                answer = record["answers"][qid][field["id"]]
                kind, status = field["type"], answer["status"]
                value = answer["value"] if status == "answered" else None
                required = "必答" if field.get("required") else "选填"
                add(f"{field['label']}【{TYPE_LABELS[kind]}；{required}】", "field")
                add(f"{ref}　作答状态：{STATUS_LABELS[status]}", "small")
                add(field.get("help"))
                if field.get("visible_when"):
                    add(condition(field["visible_when"]), "small")
                if status == "skipped":
                    add(f"跳题规则：{answer['skip_rule_id']}", "small")
                if field.get("max_selections"):
                    add(f"最多填写或选择 {field['max_selections']} 项。", "small")
                labels = {option["code"]: option["label"] for option in field.get("options", [])}
                if field.get("exclusive_options"):
                    add("须单独选择的选项：" + "、".join(labels[code] for code in field["exclusive_options"]), "small")
                for conflict in field.get("conflicts", []):
                    add("不能同时选择：" + "、".join(labels[code] for code in conflict), "small")
                if kind in {"single_select", "multi_select"}:
                    selected = ([value] if kind == "single_select" else value) if value is not None else []
                    options(field, selected)
                elif kind == "field_refs":
                    selected = set(value or [])
                    if value == []:
                        add("已作答，排除清单为空：本清单没有勾选具体字段。")
                    allowed = set(field["allowed_refs"])
                    for target in definition["questions"]:
                        refs = [f"{target['id']}.{item['id']}" for item in target["fields"] if f"{target['id']}.{item['id']}" in allowed]
                        if not refs:
                            continue
                        add(f"{target['source_number']}　{target['title']}", "field")
                        picked = sum(ref in selected for ref in refs)
                        marker = "[x]" if picked == len(refs) else "[-]" if picked else "[ ]"
                        add(f"{marker} 选择本题全部回答" + ("（部分勾选）" if 0 < picked < len(refs) else ""), "option")
                        for target_ref in refs:
                            add(f"{'[x]' if target_ref in selected else '[ ]'} {index[target_ref]['label']}（{target_ref}）", "option")
                elif kind == "ranked_values":
                    items = value if value is not None else [None]
                    if value is None:
                        add("未填写排序条目；下列为每条的原始填写结构。")
                    for position, item in enumerate(items, 1):
                        if item is not None:
                            add(f"第 {position} 位 · 重要考虑", "field")
                        for item_field in field["item_fields"]:
                            add(item_field["label"], "field")
                            item_value = item[item_field["id"]] if item is not None else None
                            if item_field["type"] == "single_select":
                                options(item_field, [item_value] if item_value is not None else [])
                            else:
                                add("填写：" + (item_value if item_value else "________（未填写）"), "option")
                else:
                    add("填写：" + (str(value) if value is not None else "________"), "option")
            add(question.get("outro"))
    return blocks


def render_questionnaire_pdf(record: dict, definition: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    blocks = questionnaire_blocks(record, definition)
    font = _pdf_font()
    base = ParagraphStyle("body", fontName=font, fontSize=10, leading=16, textColor=colors.black,
                          wordWrap="CJK", splitLongWords=True, spaceAfter=5)
    styles = {"body": base}
    for name, settings in {
        "title": dict(fontSize=18, leading=25, spaceAfter=12, keepWithNext=True),
        "section": dict(fontSize=14, leading=22, spaceBefore=16, spaceAfter=8, keepWithNext=True),
        "heading": dict(fontSize=12, leading=19, spaceBefore=12, keepWithNext=True),
        "field": dict(spaceBefore=7, keepWithNext=True),
        "small": dict(fontSize=8, leading=13),
        "option": dict(leftIndent=10, spaceAfter=3),
    }.items():
        styles[name] = ParagraphStyle(name, parent=base, **settings)
    # Paragraph markup is escaped so text such as <b> remains the user's text.
    story = [Paragraph(escape(text, quote=False).replace("\n", "<br/>"), styles[style]) for style, text in blocks]
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                                topMargin=16*mm, bottomMargin=18*mm, title=definition["title"] + "（原始答卷）", author="EPIC")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.black)
        canvas.drawString(18*mm, 10*mm, "EPIC · 原始答卷 · 开发者对照")
        canvas.drawRightString(A4[0]-18*mm, 10*mm, f"第 {doc.page} 页")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def export_questionnaire(answers_path: str | Path, definition: dict, *, force: bool = False) -> bool:
    """Publish a complete PDF next to answers.json; return False when skipped."""
    source = Path(answers_path)
    if source.name != "answers.json":
        raise ValueError("请传入 answers.json。")
    destination = source.with_name(OUTPUT_FILENAME)
    if destination.is_symlink():
        raise ValueError("输出文件不能是符号链接。")
    if destination.exists() and not force:
        if not destination.is_file():
            raise ValueError("输出路径已存在且不是文件。")
        return False
    snapshot = source.read_bytes()
    record = parse_model_json(snapshot.decode("utf-8"))
    pdf = render_questionnaire_pdf(record, definition)
    if source.read_bytes() != snapshot:
        raise ValueError("导出期间原始答卷已变化，请重新运行。")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=source.parent, prefix=".questionnaire-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(pdf)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary, destination)
        else:
            # Atomic, no-clobber publication also handles simultaneous runs.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True
