"""Deterministic rendering, approval gates, escaping and paginated PDF output."""

from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.report_pipeline import PipelineError, run_report
from backend.report_rendering import _pdf_font, export_report, load_approved_report, render_html, render_pdf
from backend.report_validation import ReportValidationError
from backend.storage import SubmissionStore
from backend.validation import normalize_submission
from tests.fixtures import payload, with_answer


ROOT = Path(__file__).resolve().parents[1]
HAS_PDF = importlib.util.find_spec("reportlab") is not None
DROID_FONT = Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
ARPHIC_FONT = Path("/usr/share/fonts/truetype/arphic-gbsn00lp/gbsn00lp.ttf")


def report_fixture():
    return {
        "00": "您目前尚无明确倾向或两边摇摆。",
        "01": [{"factor": "健康与身体", "continue_pregnancy": "您提到对健康的顾虑。", "terminate_pregnancy": "这方面尚不清楚。", "evidence_refs": ["q17.continuation_concerns"]}],
        "02": [{"analysis": "您希望澄清两种选择与本人愿望的关系。", "evidence_refs": ["q17.value_priorities", "q34.current_preference"]}],
        "03": {"recommendation": "现有依据不足以判断哪一方向更符合您的意愿。", "evidence_refs": ["q17.value_priorities"]},
    }


class Client:
    def __init__(self, draft):
        self.responses = [draft, {"decision": "pass", "issues": [], "revised_report": None}]

    def chat(self, **kwargs):
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class ApprovedRenderingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        definition = json.loads((ROOT / "questionnaire/definition.json").read_text())
        store = SubmissionStore(self.root / "submissions", definition)
        body = with_answer(payload(), "q34", "current_preference", "undecided")
        body = with_answer(body, "q17", "continuation_concerns", ["health"])
        record = store.create(normalize_submission(body, definition))
        self.revision_dir = store.root / record["submission_id"] / "v1"
        self.report = report_fixture()
        run = run_report(self.revision_dir / "model_input.json", client=Client(self.report))
        self.final_path = Path(run["run_dir"]) / "final_report.json"

    def write(self, path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_load_requires_exact_approved_content_in_either_supported_location(self):
        self.assertEqual(load_approved_report(self.final_path), self.report)
        for filename in ["run.json", "draft_report.json", "review_report.json", "final_report.json"]:
            shutil.copy2(self.final_path.parent / filename, self.revision_dir / filename)
        self.assertEqual(load_approved_report(self.revision_dir / "final_report.json"), self.report)

    def test_failed_hold_and_missing_audit_files_are_not_exportable(self):
        state_path = self.final_path.with_name("run.json")
        state = json.loads(state_path.read_text())
        for status in ["failed", "hold", "running"]:
            state["status"] = status
            self.write(state_path, state)
            with self.subTest(status=status), self.assertRaises(PipelineError):
                load_approved_report(self.final_path)
        state["status"] = "approved"
        self.write(state_path, state)
        self.final_path.with_name("review_report.json").unlink()
        with self.assertRaises(PipelineError):
            load_approved_report(self.final_path)

    def test_final_cannot_be_changed_after_review(self):
        changed = deepcopy(self.report)
        changed["00"] = "这段话没有经过审核。"
        self.write(self.final_path, changed)
        with self.assertRaises(PipelineError) as caught:
            load_approved_report(self.final_path)
        self.assertEqual(caught.exception.code, "report_content_mismatch")

    def test_changed_source_and_revoked_authorization_are_rejected(self):
        source = self.revision_dir / "answers.json"
        original = json.loads(source.read_text())
        for mutate in [lambda record: record.update(submitted_at="changed"), lambda record: record["answers"]["consent"]["report_use"].update(value="disagree")]:
            changed = deepcopy(original)
            mutate(changed)
            self.write(source, changed)
            with self.assertRaises(PipelineError):
                load_approved_report(self.final_path)

    def test_wrong_review_decision_and_invented_references_are_rejected(self):
        state_path = self.final_path.with_name("run.json")
        original = json.loads(state_path.read_text())
        state = deepcopy(original)
        state["decision"] = "revise"
        self.write(state_path, state)
        with self.assertRaises(PipelineError):
            load_approved_report(self.final_path)
        self.write(state_path, original)
        draft = deepcopy(self.report)
        draft["01"][0]["evidence_refs"] = ["q99.made_up"]
        self.write(self.final_path.with_name("draft_report.json"), draft)
        with self.assertRaises(ReportValidationError):
            load_approved_report(self.final_path)

    def test_html_escapes_every_report_text_and_preserves_line_breaks(self):
        attack = '<script>alert("x")</script> & </style>\n第二行'
        report = deepcopy(self.report)
        report["00"] = attack
        report["01"][0].update(factor=attack, continue_pregnancy=attack, terminate_pregnancy=attack)
        report["02"][0]["analysis"] = attack
        report["03"]["recommendation"] = attack
        html = render_html(report)
        self.assertNotIn('<script>alert', html)
        self.assertNotIn('<img src=x', html)
        self.assertEqual(html.count('&lt;script&gt;'), 6)
        self.assertEqual(html.count('\n第二行'), 6)
        self.assertIn('href="report.pdf"', html)
        self.assertIn('lang="zh-CN"', html)
        for demo in ["虚构演示", "林女士", "DEMO-001"]:
            self.assertNotIn(demo, html)

    def test_empty_sections_have_no_fabricated_individualized_content(self):
        report = deepcopy(self.report)
        report["01"], report["02"] = [], []
        html = render_html(report)
        self.assertIn("现有回答不足以形成有依据的个性化对照。", html)
        self.assertIn("现有回答暂不足以形成可靠的跨题分析。", html)
        self.assertIn(report["00"], html)
        self.assertIn(report["03"]["recommendation"], html)

    def test_reexport_refreshes_only_matching_ready_publication_checksums(self):
        directory = self.final_path.parent
        status_path = directory / "report_status.json"
        original = {
            "status": "ready", "updated_at": "old timestamp",
            "final_sha256": hashlib.sha256(self.final_path.read_bytes()).hexdigest(),
            "artifacts": {"report.html": "old html hash", "report.pdf": "old pdf hash", "other": "preserve"},
            "custom": {"keep": True},
        }
        self.write(status_path, original)
        with patch("backend.report_rendering.render_pdf", return_value=b"%PDF-render-test"):
            export_report(self.final_path)
            ready = json.loads(status_path.read_text())
            self.assertEqual(ready["status"], "ready")
            self.assertEqual(ready["final_sha256"], original["final_sha256"])
            self.assertEqual(ready["custom"], original["custom"])
            self.assertEqual(ready["artifacts"]["other"], "preserve")
            for name in ("report.html", "report.pdf"):
                self.assertEqual(ready["artifacts"][name], hashlib.sha256((directory / name).read_bytes()).hexdigest())
            self.assertNotEqual(ready["updated_at"], original["updated_at"])

            for status in ["hold", "failed", "blocked", "queued", "generating", "reviewing", "rendering", "interrupted"]:
                state = {**original, "status": status}
                self.write(status_path, state)
                before = status_path.read_bytes()
                export_report(self.final_path)
                with self.subTest(status=status):
                    self.assertEqual(status_path.read_bytes(), before)

            self.write(status_path, {**original, "final_sha256": "a different final report"})
            before = status_path.read_bytes()
            export_report(self.final_path)
            self.assertEqual(status_path.read_bytes(), before)
            status_path.unlink()
            export_report(self.final_path)
            self.assertFalse(status_path.exists())

    @unittest.skipUnless(HAS_PDF, "reportlab is optional for questionnaire-only environments")
    def test_export_writes_real_pdf_and_standalone_html_without_altering_sources(self):
        before = {path: path.read_bytes() for path in self.final_path.parent.iterdir()}
        paths = export_report(self.final_path)
        self.assertEqual(paths["html_path"], self.final_path.with_name("report.html"))
        self.assertEqual(paths["pdf_path"], self.final_path.with_name("report.pdf"))
        self.assertTrue(paths["pdf_path"].read_bytes().startswith(b"%PDF-"))
        self.assertGreater(paths["pdf_path"].stat().st_size, 2000)
        html = paths["html_path"].read_text()
        self.assertNotIn("回答依据", html)
        self.assertNotIn("q17.value_priorities", html)
        self.assertIn("<style>", html)
        self.assertNotIn('<link rel="stylesheet"', html)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    @unittest.skipUnless(HAS_PDF and DROID_FONT.is_file() and ARPHIC_FONT.is_file(), "font coverage regression requires the two system fonts")
    def test_cjk_fallback_missing_latin_and_digits_is_skipped(self):
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        incomplete = TTFont("IncompleteProbe", str(DROID_FONT))
        self.assertEqual(incomplete.face.charToGlyph.get(ord("E"), 0), 0)
        self.assertEqual(incomplete.face.charToGlyph.get(ord("0"), 0), 0)
        self.assertNotEqual(incomplete.face.charToGlyph.get(ord("中"), 0), 0)
        with patch.dict("os.environ", {"EPIC_PDF_FONT": str(DROID_FONT)}), \
                patch.object(pdfmetrics, "getRegisteredFontNames", return_value=[]), \
                patch.object(pdfmetrics, "registerFont") as register, \
                patch.object(pdfmetrics, "registerFontFamily"):
            self.assertEqual(_pdf_font(), "EPICCJK")
        register.assert_called_once()
        chosen = register.call_args.args[0]
        for character in "EPICabcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789年月日第版页妊娠选择报告":
            self.assertNotEqual(chosen.face.charToGlyph.get(ord(character), 0), 0, character)
        self.assertEqual(Path(chosen.face.filename), ARPHIC_FONT)

    @unittest.skipUnless(HAS_PDF and shutil.which("pdftotext"), "PDF text extraction requires reportlab and pdftotext")
    def test_long_chinese_pdf_has_multiple_pages_and_keeps_last_paragraph(self):
        report = deepcopy(self.report)
        report["01"][0]["continue_pregnancy"] = "这是需要完整保留的中文长段落，包含本人已经表达的想法和需要核对的条件。" * 150
        report["01"][0]["terminate_pregnancy"] = "另一侧仍须保持独立依据，不能将未知内容转换成否定回答。" * 100
        report["03"]["recommendation"] += " 最后一段完整结束标识。"
        path = self.root / "long.pdf"
        path.write_bytes(render_pdf(report))
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode("utf-8")
        self.assertIn("最后一段完整结束标识", text.replace("\n", "").replace(" ", ""))
        self.assertGreater(text.count("\f"), 1)
        self.assertIn("这是需要完整保留", text.replace("\n", "").replace(" ", ""))

    @unittest.skipUnless(HAS_PDF, "reportlab is optional for questionnaire-only environments")
    def test_cli_can_render_from_another_working_directory_without_llm(self):
        wrapper = "import runpy, sys\nsys.modules['llm'] = None\nscript = sys.argv.pop(1)\nrunpy.run_path(script, run_name='__main__')\n"
        result = subprocess.run([sys.executable, "-c", wrapper, str(ROOT / "scripts/render_report.py"), str(self.final_path)], cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.final_path.with_name("report.pdf").is_file())
        self.assertNotIn(self.report["00"], result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
