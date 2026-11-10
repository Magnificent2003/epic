"""Background report lifecycle and HTTP publication tests using synthetic cases."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from wsgiref.util import setup_testing_defaults

from backend.main import create_app
from backend.report_jobs import ReportJobs
from backend.report_pipeline import run_report
from backend.storage import SubmissionStore
from backend.validation import normalize_submission
from tests.fixtures import payload, with_answer


ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "questionnaire/definition.json"
SECRET = "不应进入公开状态或错误提示的测试原话"
TERMINAL = {"ready", "hold", "failed", "blocked", "interrupted"}


def sample_report():
    return {
        "00": "您目前尚无明确倾向或两边摇摆。",
        "01": [],
        "02": [],
        "03": {"recommendation": "目前不足以判断哪一方向更符合您的意愿。", "evidence_refs": ["q34.current_preference"]},
    }


class ModelReplies:
    def __init__(self, decision="pass", entered=None, release=None):
        self.decision, self.entered, self.release = decision, entered, release
        self.calls = 0

    def chat(self, *, prompt, system):
        self.calls += 1
        if self.calls == 1:
            if self.entered:
                self.entered.set()
            if self.release and not self.release.wait(10):
                raise RuntimeError("Test did not release the scripted model")
            return json.dumps(sample_report(), ensure_ascii=False)
        if self.decision == "fail":
            raise RuntimeError(SECRET)
        return json.dumps({
            "decision": self.decision,
            "issues": [] if self.decision == "pass" else [{"path": "", "reason": SECRET}],
            "revised_report": None,
        }, ensure_ascii=False)


class Runner:
    def __init__(self, *decisions, entered=None, release=None):
        self.decisions = list(decisions) or ["pass"]
        self.calls = 0
        self.entered, self.release = entered, release

    def __call__(self, model_input_path, *, definition_path=None, progress=None):
        index = self.calls
        self.calls += 1
        decision = self.decisions[min(index, len(self.decisions) - 1)]
        client = ModelReplies(decision, self.entered if index == 0 else None, self.release if index == 0 else None)
        return run_report(model_input_path, definition_path=definition_path, progress=progress, client=client)


def fake_renderer(final_path, *, definition_path=None):
    # Exercise the publication contract without requiring a PDF dependency.
    directory = Path(final_path).parent
    html = directory / "report.html"
    pdf = directory / "report.pdf"
    html.write_text('<!doctype html><html lang="zh-CN"><title>测试报告</title></html>', encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.4\n% synthetic test document\n%%EOF\n")
    return {"html_path": html, "pdf_path": pdf}


def request(app, method, path, body=None):
    environ = {}
    setup_testing_defaults(environ)
    raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, CONTENT_TYPE="application/json", CONTENT_LENGTH=str(len(raw)))
    environ["wsgi.input"] = io.BytesIO(raw)
    response = {}

    def start_response(status, headers):
        response["status"] = int(status.split()[0])
        response["headers"] = dict(headers)

    response["body"] = b"".join(app(environ, start_response))
    if response["headers"].get("Content-Type", "").startswith("application/json"):
        response["json"] = json.loads(response["body"])
    return response


class ReportJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.definition = json.loads(DEFINITION.read_text(encoding="utf-8"))
        self.store = SubmissionStore(self.root, self.definition)
        self.body = with_answer(payload(), "q34", "current_preference", "undecided")
        self.record = self.store.create(normalize_submission(self.body, self.definition))
        self.submission_id = self.record["submission_id"]

    def jobs(self, runner=None, renderer=fake_renderer):
        manager = ReportJobs(self.store, DEFINITION, runner=runner or Runner(), renderer=renderer)
        self.addCleanup(manager.close)
        return manager

    def wait(self, manager, revision=1):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = manager.status(self.submission_id, revision)
            if state["status"] in TERMINAL:
                return state
            time.sleep(.02)
        self.fail(f"Report worker did not settle: {state}")

    def test_background_work_is_deduplicated_and_published_only_when_ready(self):
        entered, release = threading.Event(), threading.Event()
        runner = Runner(entered=entered, release=release)
        manager = self.jobs(runner)
        self.addCleanup(release.set)
        state = manager.start(self.submission_id, 1)
        self.assertIn(state["status"], {"queued", "generating"})
        self.assertTrue(entered.wait(2))
        for _ in range(3):
            running = manager.start(self.submission_id, 1)
            self.assertNotIn("report_url", running)
            self.assertNotIn("pdf_url", running)
        self.assertEqual(runner.calls, 1)
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.html")
        release.set()
        ready = self.wait(manager)
        self.assertEqual(ready["status"], "ready", ready)
        self.assertIn("report_url", ready)
        self.assertIn("pdf_url", ready)
        self.assertFalse(ready["retryable"])
        self.assertIn(b"<html", manager.asset(self.submission_id, 1, "report.html"))
        self.assertTrue(manager.asset(self.submission_id, 1, "report.pdf").startswith(b"%PDF"))
        manager.start(self.submission_id, 1)
        self.assertEqual(runner.calls, 1)

    def test_failed_attempt_can_retry_without_losing_raw_answers(self):
        runner = Runner("fail", "pass")
        manager = self.jobs(runner)
        source = self.root / self.submission_id / "v1" / "answers.json"
        original = source.read_bytes()
        manager.start(self.submission_id, 1)
        failed = self.wait(manager)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["retryable"])
        self.assertNotIn(SECRET, json.dumps(failed, ensure_ascii=False))
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.pdf")
        manager.start(self.submission_id, 1)
        self.assertEqual(self.wait(manager)["status"], "ready")
        self.assertEqual(runner.calls, 2)
        self.assertEqual(source.read_bytes(), original)

    def test_hold_does_not_publish_or_automatically_retry(self):
        runner = Runner("hold")
        manager = self.jobs(runner)
        manager.start(self.submission_id, 1)
        held = self.wait(manager)
        self.assertEqual(held["status"], "hold")
        self.assertFalse(held["retryable"])
        self.assertNotIn(SECRET, json.dumps(held, ensure_ascii=False))
        self.assertNotIn("report_url", held)
        manager.start(self.submission_id, 1)
        self.assertEqual(runner.calls, 1)
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.html")

    def test_exclusion_notes_block_before_model_is_called(self):
        runner = Runner()
        body = with_answer(self.body, "q38", "exclusion_notes", SECRET)
        record = self.store.create(normalize_submission(body, self.definition))
        manager = self.jobs(runner)
        state = manager.start(record["submission_id"], 1)
        # Blocking may be detected synchronously or by the worker.
        deadline = time.monotonic() + 3
        while state["status"] not in TERMINAL and time.monotonic() < deadline:
            time.sleep(.02)
            state = manager.status(record["submission_id"], 1)
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(runner.calls, 0)
        self.assertFalse(state["retryable"])
        self.assertNotIn(SECRET, json.dumps(state, ensure_ascii=False))

    def test_revision_reports_are_isolated(self):
        runner = Runner("pass", "hold")
        manager = self.jobs(runner)
        manager.start(self.submission_id, 1)
        self.assertEqual(self.wait(manager)["status"], "ready")
        first_pdf = manager.asset(self.submission_id, 1, "report.pdf")
        self.store.revise(self.submission_id, normalize_submission(self.body, self.definition))
        manager.start(self.submission_id, 2)
        self.assertEqual(self.wait(manager, 2)["status"], "hold")
        self.assertEqual(manager.asset(self.submission_id, 1, "report.pdf"), first_pdf)
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 2, "report.pdf")

    def test_changed_answers_invalidate_previously_ready_assets(self):
        manager = self.jobs()
        manager.start(self.submission_id, 1)
        self.assertEqual(self.wait(manager)["status"], "ready")
        source = self.root / self.submission_id / "v1" / "answers.json"
        value = json.loads(source.read_text())
        value["answers"]["consent"]["report_use"]["value"] = "disagree"
        source.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.html")
        self.assertNotEqual(manager.status(self.submission_id, 1)["status"], "ready")

    def test_renderer_failure_does_not_publish_partial_html(self):
        def broken(final_path, *, definition_path=None):
            (Path(final_path).parent / "report.html").write_text(SECRET)
            raise OSError(SECRET)

        manager = self.jobs(renderer=broken)
        manager.start(self.submission_id, 1)
        state = self.wait(manager)
        self.assertEqual(state["status"], "failed")
        self.assertNotIn(SECRET, json.dumps(state, ensure_ascii=False))
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.html")

    def test_restart_marks_abandoned_work_interrupted_without_calling_a_model(self):
        directory = self.root / self.submission_id / "v1"
        (directory / "report_status.json").write_text(json.dumps({"status": "reviewing"}), encoding="utf-8")
        runner = Runner()
        manager = self.jobs(runner)
        state = manager.status(self.submission_id, 1)
        self.assertEqual(state["status"], "interrupted")
        self.assertTrue(state["retryable"])
        self.assertEqual(runner.calls, 0)
        self.assertNotIn("report_url", state)
        manager.start(self.submission_id, 1)
        self.assertEqual(self.wait(manager)["status"], "ready")
        self.assertEqual(runner.calls, 1)

    def test_changed_published_file_is_not_served(self):
        manager = self.jobs()
        manager.start(self.submission_id, 1)
        self.assertEqual(self.wait(manager)["status"], "ready")
        target = self.root / self.submission_id / "v1" / "report.html"
        target.write_text(SECRET, encoding="utf-8")
        with self.assertRaises(Exception):
            manager.asset(self.submission_id, 1, "report.html")
        self.assertEqual(manager.status(self.submission_id, 1)["status"], "blocked")


class ReportHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.body = with_answer(payload(), "q34", "current_preference", "undecided")

    def app(self, runner):
        app = create_app(storage_root=self.root, report_runner=runner, report_renderer=fake_renderer)
        self.addCleanup(app.close)
        return app

    def test_submit_returns_while_model_waits_and_assets_are_private_until_ready(self):
        entered, release = threading.Event(), threading.Event()
        runner = Runner(entered=entered, release=release)
        app = self.app(runner)
        self.addCleanup(release.set)
        submitted = request(app, "POST", "/api/submissions", self.body)
        self.assertEqual(submitted["status"], 201)
        record = submitted["json"]["submission"]
        status_url = submitted["json"]["report"]["status_url"]
        self.assertTrue(entered.wait(2))
        self.assertEqual(request(app, "GET", "/api/health")["status"], 200)
        running = request(app, "GET", status_url)
        self.assertEqual(running["status"], 200)
        self.assertIn(running["json"]["status"], {"queued", "generating", "reviewing"})
        self.assertNotEqual(request(app, "GET", status_url + ".html")["status"], 200)
        release.set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ready = request(app, "GET", status_url)["json"]
            if ready["status"] in TERMINAL:
                break
            time.sleep(.02)
        self.assertEqual(ready["status"], "ready", ready)
        for key, mime in [("report_url", "text/html"), ("pdf_url", "application/pdf")]:
            response = request(app, "GET", ready[key])
            self.assertEqual(response["status"], 200)
            self.assertTrue(response["headers"]["Content-Type"].startswith(mime))
            self.assertEqual(response["headers"]["Cache-Control"], "no-store")
            head = request(app, "HEAD", ready[key])
            self.assertEqual(head["body"], b"")
            self.assertEqual(head["headers"]["Content-Length"], str(len(response["body"])))
        self.assertIn("attachment", request(app, "GET", ready["pdf_url"])["headers"].get("Content-Disposition", ""))
        self.assertNotEqual(request(app, "GET", status_url + "/../draft_report.json")["status"], 200)
        self.assertEqual(record["revision"], 1)

    def test_retry_http_endpoint_deduplicates_active_work(self):
        entered, release = threading.Event(), threading.Event()
        runner = Runner(entered=entered, release=release)
        app = self.app(runner)
        self.addCleanup(release.set)
        response = request(app, "POST", "/api/submissions", self.body)
        url = response["json"]["report"]["status_url"]
        self.assertTrue(entered.wait(2))
        for _ in range(3):
            retried = request(app, "POST", url, {})
            self.assertEqual(retried["status"], 202)
        self.assertEqual(runner.calls, 1)


if __name__ == "__main__":
    unittest.main()
