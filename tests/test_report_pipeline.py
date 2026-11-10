"""Report generation/review integration with local files and scripted models."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.report_pipeline import PipelineError, run_report
from backend.storage import SubmissionStore
from backend.translation import model_input_text
from backend.validation import normalize_submission
from tests.fixtures import payload, with_answer


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TEXT = "仅供测试的敏感原话，不应泄露到运行记录"
PROVIDER_SECRET = "模拟服务异常中的私密请求和密钥，不能写入日志"


def draft_report():
    return {
        "00": "您目前尚无明确倾向或两边摇摆。",
        "01": [{
            "factor": "健康方面的顾虑",
            "continue_pregnancy": "您提到本人或胎儿健康方面的顾虑。",
            "terminate_pregnancy": "从现有回答看，这方面尚不清楚。",
            "evidence_refs": ["q17.continuation_concerns"],
        }],
        "02": [{
            "analysis": "您尚无明确倾向，同时希望尽快获得情绪支持；两者是否有关仍待您核对。",
            "evidence_refs": ["q34.current_preference", "q36.emotional_support_preference"],
        }],
        "03": {
            "recommendation": "本题未提供价值排序，目前不足以判断哪一方向更符合您的意愿。",
            "evidence_refs": ["q17.value_priorities", "q34.current_preference"],
        },
    }


def pass_review():
    return {"decision": "pass", "issues": [], "revised_report": None}


class ScriptedClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, *, prompt, system):
        self.calls.append({"prompt": prompt, "system": system})
        if not self.responses:
            raise AssertionError("Unexpected model invocation")
        response = self.responses.pop(0)
        if callable(response):
            response = response()
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


class ReportPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.definition = json.loads((ROOT / "questionnaire/definition.json").read_text(encoding="utf-8"))
        self.store = SubmissionStore(self.root / "submissions", self.definition)
        self.body = with_answer(payload(), "q01", "age_years", 26)
        self.body = with_answer(self.body, "q17", "continuation_concerns", ["health"])
        self.body = with_answer(self.body, "q34", "current_preference", "undecided")
        self.body = with_answer(self.body, "q36", "emotional_support_preference", "soon")
        self.body = with_answer(self.body, "q38", "report_notes", PRIVATE_TEXT)
        self.input_path = self.make_case(self.body)

    def make_case(self, body):
        record = self.store.create(normalize_submission(body, self.definition))
        return self.store.root / record["submission_id"] / "v1" / "model_input.json"

    def read(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write(self, path, value):
        Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def assert_failed(self, result):
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"]["code"])
        self.assertTrue(result["error"]["message"])
        directory = Path(result["run_dir"])
        self.assertTrue(directory.is_absolute())
        self.assertFalse((directory / "final_report.json").exists())
        self.assertEqual(self.read(directory / "run.json")["status"], "failed")
        for secret in [PRIVATE_TEXT, PROVIDER_SECRET]:
            self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
            self.assertNotIn(secret, (directory / "run.json").read_text(encoding="utf-8"))

    def assert_preflight_blocked(self, path):
        client = ScriptedClient(draft_report(), pass_review())
        with self.assertRaises(PipelineError) as caught:
            run_report(path, client=client)
        self.assertTrue(caught.exception.code)
        self.assertTrue(caught.exception.message)
        self.assertEqual(client.calls, [])
        self.assertFalse((path.parent / "reports").exists())

    def test_pass_publishes_draft_and_preserves_inputs(self):
        source_paths = [self.input_path, self.input_path.parent / "answers.json"]
        before = {path: path.read_bytes() for path in source_paths}
        draft = draft_report()
        client = ScriptedClient(draft, pass_review())
        result = run_report(self.input_path, client=client)
        self.assertEqual(result["status"], "approved")
        directory = Path(result["run_dir"])
        self.assertEqual(directory.parent, self.input_path.parent / "reports")
        self.assertEqual(directory.name, result["run_id"])
        self.assertEqual(self.read(directory / "draft_report.json"), draft)
        self.assertEqual(self.read(directory / "review_report.json"), pass_review())
        self.assertEqual(self.read(directory / "final_report.json"), draft)
        self.assertEqual(self.read(directory / "run.json")["status"], "approved")
        self.assertEqual(len(client.calls), 2)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_review_has_same_available_evidence_and_a_separate_system_prompt(self):
        client = ScriptedClient(draft_report(), pass_review())
        run_report(self.input_path, client=client)
        generation, review = [json.loads(call["prompt"]) for call in client.calls]
        self.assertEqual(generation["case_input"], review["case_input"])
        self.assertEqual(generation["case_input"], model_input_text(self.read(self.input_path)))
        self.assertEqual(generation["report_schema"], review["report_schema"])
        self.assertNotIn("draft_report", generation)
        self.assertEqual(review["draft_report"], draft_report())
        self.assertIn("review_schema", review)
        for call, filename in zip(client.calls, ["generate_report.md", "review_report.md"]):
            self.assertEqual(call["system"].strip(), (ROOT / "prompt" / filename).read_text(encoding="utf-8").strip())
            self.assertNotIn(PRIVATE_TEXT, call["system"])
        self.assertNotEqual(client.calls[0]["system"], client.calls[1]["system"])

    def test_revise_publishes_only_the_complete_validated_revision(self):
        draft = draft_report()
        revised = deepcopy(draft)
        revised["03"]["recommendation"] = "您可以先澄清最看重的因素；目前不足以判断哪一方向更符合您的意愿。"
        review = {"decision": "revise", "issues": [{"path": "/03/recommendation", "reason": "将措辞改为面向本人的条件性说明。"}], "revised_report": revised}
        result = run_report(self.input_path, client=ScriptedClient(draft, review))
        directory = Path(result["run_dir"])
        self.assertEqual(result["status"], "approved")
        self.assertEqual(self.read(directory / "draft_report.json"), draft)
        self.assertEqual(self.read(directory / "final_report.json"), revised)
        self.assertEqual(self.read(directory / "review_report.json"), review)

    def test_hold_saves_review_without_a_final_report(self):
        review = {"decision": "hold", "issues": [{"path": "", "reason": "关键陈述需要进一步核对。"}], "revised_report": None}
        result = run_report(self.input_path, client=ScriptedClient(draft_report(), review))
        directory = Path(result["run_dir"])
        self.assertEqual(result["status"], "hold")
        self.assertTrue((directory / "draft_report.json").exists())
        self.assertEqual(self.read(directory / "review_report.json"), review)
        self.assertFalse((directory / "final_report.json").exists())

    def test_bad_generation_never_reaches_review_or_publication(self):
        invented_ref = draft_report()
        invented_ref["01"][0]["evidence_refs"] = ["q99.unknown_field"]
        for response in [RuntimeError(PROVIDER_SECRET), "not JSON " + PRIVATE_TEXT, invented_ref]:
            with self.subTest(response_type=type(response).__name__):
                client = ScriptedClient(response, pass_review())
                result = run_report(self.input_path, client=client)
                self.assert_failed(result)
                self.assertEqual(len(client.calls), 1)
                self.assertFalse((Path(result["run_dir"]) / "review_report.json").exists())

    def test_bad_review_never_publishes_the_draft(self):
        invalid_revised = draft_report()
        invalid_revised["03"]["evidence_refs"] = ["q99.unknown_field"]
        responses = [
            RuntimeError(PROVIDER_SECRET),
            "not JSON " + PRIVATE_TEXT,
            {"decision": "invented", "issues": [], "revised_report": None},
            {"decision": "revise", "issues": [{"path": "/03", "reason": "修正依据。"}], "revised_report": invalid_revised},
        ]
        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                client = ScriptedClient(draft_report(), response)
                result = run_report(self.input_path, client=client)
                self.assert_failed(result)
                self.assertEqual(len(client.calls), 2)
                self.assertEqual(self.read(Path(result["run_dir"]) / "draft_report.json"), draft_report())

    def test_blocked_translation_and_unresolved_exclusions_make_no_model_calls(self):
        body = with_answer(self.body, "q38", "exclusion_notes", PRIVATE_TEXT)
        self.assert_preflight_blocked(self.make_case(body))
        artifact = self.read(self.input_path)
        artifact["status"] = "blocked"
        self.write(self.input_path, artifact)
        self.assert_preflight_blocked(self.input_path)

    def test_missing_authorization_is_rechecked_against_original_answers(self):
        answers_path = self.input_path.parent / "answers.json"
        record = self.read(answers_path)
        record["answers"]["consent"]["report_use"] = {"status": "answered", "value": "disagree"}
        self.write(answers_path, record)
        self.assert_preflight_blocked(self.input_path)

    def test_changed_or_tampered_translation_requires_regeneration(self):
        for field in ["lines", "instructions"]:
            path = self.make_case(self.body)
            artifact = self.read(path)
            artifact[field].append("伪造的可用信息")
            self.write(path, artifact)
            with self.subTest(field=field):
                self.assert_preflight_blocked(path)
        path = self.make_case(self.body)
        answers_path = path.parent / "answers.json"
        record = self.read(answers_path)
        record["answers"]["q01"]["age_years"]["value"] = 27
        self.write(answers_path, record)
        self.assert_preflight_blocked(path)

    def test_missing_source_is_not_accepted_even_with_ready_translation(self):
        (self.input_path.parent / "answers.json").unlink()
        self.assert_preflight_blocked(self.input_path)

    def test_excluded_or_unanswered_fields_cannot_be_cited(self):
        body = with_answer(self.body, "q38", "excluded_report_fields", ["q38.report_notes"])
        path = self.make_case(body)
        for unavailable_ref in ["q38.report_notes", "q02.last_menstrual_period"]:
            draft = draft_report()
            draft["01"][0]["evidence_refs"] = [unavailable_ref]
            client = ScriptedClient(draft, pass_review())
            result = run_report(path, client=client)
            self.assert_failed(result)
            self.assertEqual(len(client.calls), 1)
            self.assertNotIn(PRIVATE_TEXT, client.calls[0]["prompt"])

    def test_source_changes_during_generation_stop_before_review(self):
        def generation():
            path = self.input_path.parent / "answers.json"
            record = self.read(path)
            record["answers"]["consent"]["report_use"]["value"] = "disagree"
            self.write(path, record)
            return draft_report()

        client = ScriptedClient(generation, pass_review())
        result = run_report(self.input_path, client=client)
        self.assert_failed(result)
        self.assertEqual(len(client.calls), 1)

    def test_input_changes_during_review_prevent_final_publication(self):
        def review():
            artifact = self.read(self.input_path)
            artifact["lines"].append("运行期间改变的资料")
            self.write(self.input_path, artifact)
            return pass_review()

        client = ScriptedClient(draft_report(), review)
        result = run_report(self.input_path, client=client)
        self.assert_failed(result)
        self.assertEqual(len(client.calls), 2)

    def test_repeated_runs_are_isolated_and_hold_does_not_reuse_old_final(self):
        first = run_report(self.input_path, client=ScriptedClient(draft_report(), pass_review()))
        first_dir = Path(first["run_dir"])
        first_files = {p.name: p.read_bytes() for p in first_dir.iterdir()}
        review = {"decision": "hold", "issues": [{"path": "", "reason": "需复核关键陈述。"}], "revised_report": None}
        second = run_report(self.input_path, client=ScriptedClient(draft_report(), review))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.assertFalse((Path(second["run_dir"]) / "final_report.json").exists())
        self.assertEqual({p.name: p.read_bytes() for p in first_dir.iterdir()}, first_files)

    def check_cli(self):
        # A poisoned module makes any accidental SDK import fail, even when
        # this test runs on a machine with all model dependencies installed.
        wrapper = "import runpy, sys\nsys.modules['llm'] = None\nscript = sys.argv.pop(1)\nrunpy.run_path(script, run_name='__main__')\n"
        return subprocess.run(
            [sys.executable, "-c", wrapper, str(ROOT / "scripts/run_report.py"), str(self.input_path), "--check"],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )

    def test_cli_check_works_from_another_directory_without_importing_llm(self):
        result = self.check_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("检查通过", result.stdout)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        self.assertFalse((self.input_path.parent / "reports").exists())

    def test_cli_check_rejects_bad_source_without_disclosing_it(self):
        (self.input_path.parent / "answers.json").write_text(PRIVATE_TEXT, encoding="utf-8")
        result = self.check_cli()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.input_path.parent / "reports").exists())

    def test_cli_exit_codes_and_messages_do_not_expose_report_or_review_text(self):
        spec = importlib.util.spec_from_file_location("epic_report_cli_for_test", ROOT / "scripts/run_report.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        common = {
            "run_dir": str(self.root / "test-run"), "stage": "review",
            "draft_report": {"00": PRIVATE_TEXT},
            "review_report": {"issues": [{"path": "", "reason": PRIVATE_TEXT}]},
        }
        cases = [
            ({**common, "status": "approved", "decision": "pass"}, 0),
            ({**common, "status": "hold", "decision": "hold"}, 3),
            ({**common, "status": "failed", "error": {"code": "model_call_failed", "message": "模型调用失败。"}}, 1),
        ]
        for state, expected in cases:
            with self.subTest(status=state["status"]):
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(cli, "run_report", return_value=state), patch.object(sys, "argv", ["run_report.py", str(self.input_path)]), redirect_stdout(stdout), redirect_stderr(stderr):
                    actual = cli.main()
                self.assertEqual(actual, expected)
                self.assertNotIn(PRIVATE_TEXT, stdout.getvalue() + stderr.getvalue())

    def test_default_client_uses_existing_invoker_configuration_without_overrides(self):
        client = ScriptedClient(draft_report(), pass_review())
        client.settings = SimpleNamespace(provider="dashscope", model="configured-model", api_key=PROVIDER_SECRET, base_url=PRIVATE_TEXT)
        factory = Mock(return_value=client)
        with patch.dict(sys.modules, {"llm": SimpleNamespace(LLMInvoker=factory)}):
            result = run_report(self.input_path)
        factory.assert_called_once_with()
        self.assertEqual(result["status"], "approved")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result["model"], {"provider": "dashscope", "name": "configured-model"})
        state_text = (Path(result["run_dir"]) / "run.json").read_text(encoding="utf-8")
        for sensitive in [PRIVATE_TEXT, PROVIDER_SECRET, "api_key", "base_url"]:
            self.assertNotIn(sensitive, state_text)

    def test_default_client_rejects_other_provider_before_any_model_call(self):
        client = ScriptedClient(draft_report(), pass_review())
        client.settings = SimpleNamespace(provider="openai", model="configured-model")
        factory = Mock(return_value=client)
        with patch.dict(sys.modules, {"llm": SimpleNamespace(LLMInvoker=factory)}):
            result = run_report(self.input_path)
        factory.assert_called_once_with()
        self.assert_failed(result)
        self.assertEqual(result["error"]["code"], "provider_mismatch")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
