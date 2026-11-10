"""Preserve the actual questionnaire, including data intentionally omitted from reports."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from backend.questionnaire_pdf import export_questionnaire, questionnaire_blocks
from backend.validation import ValidationError, normalize_submission
from tests.fixtures import answered, payload, with_answer


ROOT = Path(__file__).resolve().parents[1]


class QuestionnairePDFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = json.loads((ROOT / "questionnaire/definition.json").read_text())

    def setUp(self):
        body = with_answer(payload(), "q01", "age_years", 26)
        body = with_answer(body, "q02", "gestational_weeks", 0)
        body["answers"]["q02"]["last_menstrual_period"] = {"status": "declined", "value": None}
        body = with_answer(body, "q05", "pregnancies_total", 1)
        body["answers"]["q11"] = {"work_school_support": {"status": "not_applicable", "value": None}}
        body = with_answer(body, "q17", "continuation_concerns", ["health"])
        body = with_answer(body, "q17", "value_priorities", [
            {"factor": "先完成学业", "relation": "uncertain", "conditions": ""},
            {"factor": "身体状况", "relation": "supports_both", "conditions": "先咨询医生"},
        ])
        body = with_answer(body, "q38", "report_notes", "原话 <b>不作为标记</b> & 保留\n第二行")
        body = with_answer(body, "q38", "excluded_report_fields", ["q01.age_years", "q38.report_notes"])
        self.record = normalize_submission(body, self.definition)
        self.record.update(submission_id="31176d5e-2a6c-4afb-90c3-113bbf94ddea", revision=1,
                           submitted_at="2026-11-09T01:32:27.684495Z")

    def text(self, record=None):
        return "\n".join(text for _, text in questionnaire_blocks(record or self.record, self.definition))

    def test_question_wording_all_options_and_answer_states_survive(self):
        text = self.text()
        for question in self.definition["questions"]:
            self.assertIn(question["title"], text)
            for field in question["fields"]:
                self.assertIn(field["label"], text)
                for option in field.get("options", []):
                    self.assertIn(option["label"], text)
        self.assertIn("2026-11-09 09:32:27（北京时间", text)
        self.assertIn("q02.last_menstrual_period　作答状态：不愿回答", text)
        self.assertIn("q05.deliveries_count　作答状态：按条件跳过", text)
        self.assertIn("q11.work_school_support　作答状态：不适用", text)
        self.assertIn("q34.current_preference　作答状态：未填写", text)
        self.assertIn("填写：0", text)
        self.assertIn("[x] 本人或胎儿健康", text)
        self.assertIn("[ ] 经济住房", text)

    def test_exclusions_do_not_remove_raw_answers_or_rank_structure(self):
        before = deepcopy(self.record)
        text = self.text()
        self.assertIn("填写：26", text)
        self.assertIn("原话 <b>不作为标记</b> & 保留\n第二行", text)
        self.assertLess(text.index("填写：先完成学业"), text.index("填写：身体状况"))
        self.assertIn("第 2 位 · 重要考虑", text)
        self.assertIn("[x] 两种选择都能满足", text)
        self.assertIn("填写：________（未填写）", text)
        self.assertIn("[ ] 更支持终止", text)
        self.assertIn("[x] 年龄（周岁）（q01.age_years）", text)
        self.assertEqual(self.record, before)

    def test_empty_exclusion_list_and_unanswered_are_distinct(self):
        record = deepcopy(self.record)
        record["answers"]["q38"]["excluded_report_fields"] = answered([])
        self.assertIn("已作答，排除清单为空", self.text(record))
        record["answers"]["q38"]["excluded_report_fields"] = {"status": "unanswered", "value": None}
        text = self.text(record)
        self.assertIn("q38.excluded_report_fields　作答状态：未填写", text)
        self.assertNotIn("已作答，排除清单为空", text)

    def test_invalid_versions_and_inconsistent_raw_states_are_rejected(self):
        record = deepcopy(self.record)
        record["questionnaire_version"] = "0.0.0"
        with self.assertRaises(ValidationError):
            self.text(record)
        record = deepcopy(self.record)
        record["answers"]["q05"]["deliveries_count"] = answered(1)
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.text(record)

    def test_real_pdf_has_escaped_text_pagination_and_skip_preserves_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "answers.json"
            source.write_text(json.dumps(self.record, ensure_ascii=False), encoding="utf-8")
            original = source.read_bytes()
            self.assertTrue(export_questionnaire(source, self.definition))
            pdf = source.with_name("questionnaire.pdf")
            snapshot, modified = pdf.read_bytes(), pdf.stat().st_mtime_ns
            self.assertTrue(snapshot.startswith(b"%PDF-"))
            self.assertFalse(export_questionnaire(source, self.definition))
            self.assertEqual((pdf.read_bytes(), pdf.stat().st_mtime_ns), (snapshot, modified))
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(export_questionnaire(source, self.definition, force=True))
            if shutil.which("pdftotext"):
                text = subprocess.check_output(["pdftotext", "-raw", str(pdf), "-"], text=True)
                self.assertGreater(text.count("\f"), 1)
                compact = "".join(text.split())
                self.assertIn("<b>不作为标记</b>&保留", compact)
                self.assertIn("2026-11-0909:32:27", compact)
                self.assertIn("q38.report_action", compact)

    def test_batch_runs_from_another_directory_and_continues_after_bad_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "nested" / "submission" / "v1"
            bad = root / "bad" / "v1"
            good.mkdir(parents=True)
            bad.mkdir(parents=True)
            (good / "answers.json").write_text(json.dumps(self.record), encoding="utf-8")
            (bad / "answers.json").write_text('{"broken":', encoding="utf-8")
            command = [sys.executable, str(ROOT / "scripts/export_questionnaires.py"), "--root", str(root)]
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("生成 1 份，跳过 0 份，失败 1 份", result.stdout)
            self.assertTrue((good / "questionnaire.pdf").is_file())
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertIn("生成 0 份，跳过 1 份，失败 1 份", result.stdout)


if __name__ == "__main__":
    unittest.main()
