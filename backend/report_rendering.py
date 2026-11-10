"""Render approved reports deterministically as standalone HTML and a real PDF."""

from __future__ import annotations

from datetime import datetime
import hashlib
from html import escape
from io import BytesIO
import os
from pathlib import Path
import tempfile
from threading import Lock
from zoneinfo import ZoneInfo

from .report_pipeline import PROJECT_ROOT, PipelineError, _now, _write_json, prepare_case
from .report_validation import ReportValidationError, parse_model_json, validate_report, validate_review


FOOTER = "本报告由智能体根据本次获准使用的回答生成并经模型审读，用于帮助您梳理个人意愿与选择。不替代医护人员的诊断与咨询，最终选择由您作出。"
EMPTY_COMPARISON = "现有回答不足以形成有依据的个性化对照。"
EMPTY_ANALYSIS = "现有回答暂不足以形成可靠的跨题分析。"
FONT_LOCK = Lock()
PDF_REQUIRED_CHARACTERS = "".join(chr(code) for code in range(32, 127)) + "年月日第版页个人妊娠选择支持报告：；，。？！（）「」“”·—"


def _read(path: Path) -> dict:
    try:
        return parse_model_json(path.read_text(encoding="utf-8"))
    except OSError:
        raise PipelineError("report_unavailable", "报告文件尚未完整生成，暂不能查看或导出。") from None


def _revision_directory(final_path: Path) -> Path:
    if final_path.name != "final_report.json":
        raise PipelineError("report_filename", "请传入经过审核的 final_report.json。")
    directory = final_path.parent
    if (directory / "model_input.json").is_file():
        return directory
    if directory.parent.name == "reports" and (directory.parent.parent / "model_input.json").is_file():
        return directory.parent.parent
    raise PipelineError("report_source_missing", "报告须位于答卷版本目录或该版本的 reports 运行目录中。")


def _approved_bundle(final_path: str | Path, definition_path: str | Path | None = None) -> dict:
    path = Path(final_path).resolve()
    directory = path.parent
    revision_dir = _revision_directory(path)
    case = prepare_case(revision_dir / "model_input.json", definition_path)
    names = ("run.json", "draft_report.json", "review_report.json", "final_report.json")
    snapshots = {}
    documents = {}
    for name in names:
        source = directory / name
        try:
            raw = source.read_bytes()
            documents[name] = parse_model_json(raw.decode("utf-8"))
        except (OSError, UnicodeError):
            raise PipelineError("report_unavailable", "报告文件尚未完整生成，暂不能查看或导出。") from None
        snapshots[source] = hashlib.sha256(raw).hexdigest()
    state, draft, review, final = (documents[name] for name in names)
    if state.get("status") != "approved" or review.get("decision") not in {"pass", "revise"}:
        raise PipelineError("report_not_approved", "这份报告尚未通过审核，暂不能查看或导出。")
    if state.get("decision") != review["decision"]:
        raise PipelineError("report_state_mismatch", "报告状态与审核结论不一致，暂不能查看或导出。")
    if state.get("source_sha256") != {source.name: digest for source, digest in case.source_hashes.items()}:
        raise PipelineError("report_source_changed", "报告依据已发生变化，请重新生成报告。")
    validate_report(draft, case.allowed_refs)
    validate_review(review, draft, case.allowed_refs)
    validate_report(final, case.allowed_refs)
    expected = draft if review["decision"] == "pass" else review["revised_report"]
    if final != expected:
        raise PipelineError("report_content_mismatch", "最终报告与经过审核的内容不一致。")
    case.check_unchanged()
    for source, digest in snapshots.items():
        try:
            unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == digest
        except OSError:
            unchanged = False
        if not unchanged:
            raise PipelineError("report_changed", "报告文件正在变化，请稍后重试。")
    record = _read(revision_dir / "answers.json")
    return {"report": final, "state": state, "revision": record.get("revision"), "directory": directory}


def load_approved_report(final_path: str | Path, definition_path: str | Path | None = None) -> dict:
    """Load only the current, authorized and reviewed report; never call an LLM."""
    return _approved_bundle(final_path, definition_path)["report"]


def _meta(generated_at: str | None, revision: int | None) -> str:
    parts = []
    if generated_at:
        try:
            instant = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            if instant.tzinfo is not None:
                parts.append(instant.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日"))
        except (ValueError, TypeError):
            pass
    if type(revision) is int and revision > 0:
        parts.append(f"第 {revision} 版答卷")
    return " · ".join(parts)


def render_html(report: dict, *, generated_at: str | None = None, revision: int | None = None) -> str:
    """Map validated data to markup without interpretation or rewriting."""
    css = (PROJECT_ROOT / "app/report.css").read_text(encoding="utf-8")
    rows = []
    for item in report["01"]:
        rows.append(
            '<tr><th scope="row"><span class="report-text">' + escape(item["factor"]) + '</span></th>'
            + '<td data-label="继续妊娠"><p class="report-text">' + escape(item["continue_pregnancy"]) + '</p></td>'
            + '<td data-label="终止妊娠"><p class="report-text">' + escape(item["terminate_pregnancy"]) + '</p></td></tr>'
        )
    comparisons = (
        '<div class="comparison-wrap"><table class="comparison"><caption class="sr-only">两种选择对本人关注因素的意义</caption>'
        '<thead><tr><th scope="col">您关注的因素</th><th scope="col">继续妊娠</th><th scope="col">终止妊娠</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    ) if rows else f'<p class="empty-state">{EMPTY_COMPARISON}</p>'
    analyses = "".join(
        '<article class="analysis-item"><p class="report-text">' + escape(item["analysis"]) + '</p></article>'
        for item in report["02"]
    ) or f'<p class="empty-state">{EMPTY_ANALYSIS}</p>'
    metadata = escape(_meta(generated_at, revision))
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>您的个人妊娠选择支持报告 · EPIC</title><style>{css}</style></head>
<body><nav class="report-toolbar" aria-label="报告操作"><a class="brand" href="/" aria-label="EPIC 首页"><span class="brand-mark" aria-hidden="true">E</span>EPIC <span>个人选择支持</span></a>
<div class="toolbar-actions"><button class="button secondary" type="button" onclick="window.print()">打印</button><a class="button primary" href="report.pdf" download="EPIC-个人支持报告.pdf">下载 PDF</a></div></nav>
<main class="report-page"><header class="report-header"><div class="eyebrow">留一点空间，听见自己的想法</div><h1>您的个人妊娠<br class="title-break">选择支持报告</h1><p class="report-intro">整理您已经表达的想法，保留仍待澄清的部分。</p><p class="report-meta">{metadata}</p><span class="header-orbit" aria-hidden="true"></span></header>
<section class="current-section" aria-labelledby="current-heading"><div class="section-label"><span>00</span><h2 id="current-heading">您目前的倾向</h2></div><p class="report-text current-text">{escape(report['00'])}</p></section>
<section class="report-section" aria-labelledby="choices-heading"><div class="section-label"><span>01</span><h2 id="choices-heading">两种选择分别对您意味着什么</h2></div>{comparisons}</section>
<section class="report-section" aria-labelledby="analysis-heading"><div class="section-label"><span>02</span><h2 id="analysis-heading">综合分析</h2></div><div class="analyses">{analyses}</div></section>
<section class="report-section advice-section" aria-labelledby="advice-heading"><div class="section-label"><span>03</span><h2 id="advice-heading">关于是否继续妊娠的建议</h2></div><div class="recommendation"><p class="report-text">{escape(report['03']['recommendation'])}</p></div></section>
<footer class="report-footer"><span class="footer-mark" aria-hidden="true">EPIC</span><p>{FOOTER}</p></footer></main>
<p class="privacy-note">这份报告包含您的个人回答，请在方便且私密的地方阅读和保存。</p></body></html>'''


def _pdf_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    def has_report_glyphs(font) -> bool:
        glyphs = getattr(font.face, "charToGlyph", {})
        return all(glyphs.get(ord(character), 0) != 0 for character in PDF_REQUIRED_CHARACTERS)

    with FONT_LOCK:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if "EPICCJK" in registered and has_report_glyphs(pdfmetrics.getFont("EPICCJK")):
            return "EPICCJK"
        candidates = [
            os.environ.get("EPIC_PDF_FONT", ""),
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/arphic-gbsn00lp/gbsn00lp.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ]
        for filename in candidates:
            if filename and Path(filename).is_file():
                try:
                    # Some CJK fallback fonts intentionally contain no Latin or
                    # digit glyphs. Extractable PDF text can still look blank.
                    # Also avoid reusing a previously registered incomplete face.
                    name = "EPICCJK" if "EPICCJK" not in registered else "EPICCJK-" + hashlib.sha256(filename.encode()).hexdigest()[:10]
                    font = TTFont(name, filename)
                    if not has_report_glyphs(font):
                        continue
                    pdfmetrics.registerFont(font)
                    pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
                    return name
                except Exception:
                    continue
        if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"


def render_pdf(report: dict, *, generated_at: str | None = None, revision: int | None = None) -> bytes:
    """Lay out the exact report text with CJK wrapping and automatic pagination."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        raise PipelineError("pdf_dependency", "请安装 requirements.txt 中的 PDF 导出依赖。") from None
    font = _pdf_font()
    dark, green, muted = colors.HexColor("#243d35"), colors.HexColor("#385c49"), colors.HexColor("#68716a")
    base = ParagraphStyle("Body", fontName=font, fontSize=10, leading=17, textColor=dark, wordWrap="CJK", splitLongWords=True, spaceAfter=8, alignment=TA_LEFT)
    title = ParagraphStyle("Title", parent=base, fontSize=23, leading=32, spaceAfter=12)
    heading = ParagraphStyle("Heading", parent=base, fontSize=13, leading=22, textColor=green, spaceBefore=18, spaceAfter=9, keepWithNext=True)
    small = ParagraphStyle("Small", parent=base, fontSize=8, leading=12, textColor=muted, spaceAfter=7)
    cell = ParagraphStyle("Cell", parent=base, fontSize=9.2, leading=15.5, spaceAfter=3)

    def paragraph(text, style=base):
        return Paragraph(escape(text, quote=False).replace("\n", "<br/>"), style)

    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=20*mm,
                            title="个人妊娠选择支持报告", author="EPIC", pageCompression=1)
    story = [paragraph("EPIC · 个人选择支持", small), paragraph("您的个人妊娠选择支持报告", title), paragraph(_meta(generated_at, revision), small), Spacer(1, 3*mm)]
    story.extend([paragraph("00  您目前的倾向", heading), paragraph(report["00"])])
    story.append(paragraph("01  两种选择分别对您意味着什么", heading))
    if report["01"]:
        data = [[paragraph("您关注的因素", cell), paragraph("继续妊娠", cell), paragraph("终止妊娠", cell)]]
        for item in report["01"]:
            data.append([paragraph(item["factor"], cell), paragraph(item["continue_pregnancy"], cell), paragraph(item["terminate_pregnancy"], cell)])
        width = A4[0] - 36*mm
        table = Table(data, colWidths=[width*.22, width*.39, width*.39], repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf0e8")),
            ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f7f8f3")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ("LINEBELOW", (0, 0), (-1, -1), .5, colors.HexColor("#dce1d6")),
        ]))
        story.append(table)
    else:
        story.append(paragraph(EMPTY_COMPARISON))
    story.append(paragraph("02  综合分析", heading))
    for item in report["02"]:
        story.append(paragraph(item["analysis"]))
        story.append(Spacer(1, 2*mm))
    if not report["02"]:
        story.append(paragraph(EMPTY_ANALYSIS))
    story.extend([paragraph("03  关于是否继续妊娠的建议", heading), paragraph(report["03"]["recommendation"])])
    story.extend([Spacer(1, 8*mm), paragraph(FOOTER, small)])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dce1d6"))
        canvas.line(18*mm, 15*mm, A4[0]-18*mm, 15*mm)
        canvas.setFont(font, 8)
        canvas.setFillColor(muted)
        canvas.drawString(18*mm, 10*mm, "EPIC · 个人支持报告")
        canvas.drawRightString(A4[0]-18*mm, 10*mm, f"第 {document.page} 页")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def _write_bytes(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".render-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _refresh_ready_artifacts(directory: Path) -> None:
    """Keep an existing publication's hashes current after a legitimate re-export."""
    status_path = directory / "report_status.json"
    try:
        snapshot = status_path.read_bytes()
        state = parse_model_json(snapshot.decode("utf-8"))
        if state.get("status") != "ready":
            return
        final_digest = hashlib.sha256((directory / "final_report.json").read_bytes()).hexdigest()
        if state.get("final_sha256") != final_digest or not isinstance(state.get("artifacts"), dict):
            return
        artifacts = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in ("report.html", "report.pdf")}
        # Do not overwrite a status changed by a concurrent generation/review.
        if status_path.read_bytes() != snapshot:
            return
        if hashlib.sha256((directory / "final_report.json").read_bytes()).hexdigest() != final_digest:
            return
    except (OSError, UnicodeError, ReportValidationError):
        return
    state["artifacts"] = {**state["artifacts"], **artifacts}
    state["updated_at"] = _now()
    _write_json(status_path, state)


def export_report(final_path: str | Path, *, definition_path: str | Path | None = None) -> dict[str, Path]:
    """Write HTML and PDF beside the final report after approval/freshness checks."""
    bundle = _approved_bundle(final_path, definition_path)
    report = bundle["report"]
    options = {"generated_at": bundle["state"].get("finished_at"), "revision": bundle["revision"]}
    html = render_html(report, **options).encode("utf-8")
    pdf = render_pdf(report, **options)
    if _approved_bundle(final_path, definition_path)["report"] != report:
        raise PipelineError("report_changed", "报告内容已变化，请重新导出。")
    html_path, pdf_path = bundle["directory"] / "report.html", bundle["directory"] / "report.pdf"
    _write_bytes(pdf_path, pdf)
    _write_bytes(html_path, html)
    _refresh_ready_artifacts(bundle["directory"])
    return {"html_path": html_path, "pdf_path": pdf_path}
