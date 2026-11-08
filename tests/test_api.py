import io
import json
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backend.main import create_app
from tests.fixtures import definition, payload, with_answer


ROOT = Path(__file__).resolve().parents[1]


def request(app, method, path, body=None, raw=None, content_type="application/json", content_length=None):
    environ = {}
    setup_testing_defaults(environ)
    data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b"")
    environ.update({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(data)) if content_length is None else content_length,
        "wsgi.input": io.BytesIO(data),
    })
    status_headers = {}

    def start_response(status, headers, exc_info=None):
        status_headers["status"] = int(status.split()[0])
        status_headers["headers"] = dict(headers)

    result = app(environ, start_response)
    try:
        raw_response = b"".join(result)
    finally:
        if hasattr(result, "close"):
            result.close()
    return status_headers["status"], json.loads(raw_response), status_headers["headers"]


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.definition_path = self.root / "definition.json"
        self.definition_path.write_text(json.dumps(definition()), encoding="utf-8")
        self.storage_root = self.root / "submissions"
        self.app = create_app(definition_path=self.definition_path, storage_root=self.storage_root)

    def test_health_and_questionnaire_are_readable_without_submissions(self):
        status, _, _ = request(self.app, "GET", "/api/health")
        self.assertEqual(status, 200)
        status, data, _ = request(self.app, "GET", "/api/questionnaire")
        self.assertEqual(status, 200)
        self.assertEqual(data["questionnaire_id"], "epic_pregnancy_decision")
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(list(self.storage_root.glob("*/v*/answers.json")), [])

    def test_validate_does_not_write_and_returns_normalized_answers(self):
        body = with_answer(payload(), "q04", "partner_status", "no_partner")
        body = with_answer(body, "q29", "partner_attitude", "continue")
        status, data, _ = request(self.app, "POST", "/api/validate", body)
        self.assertEqual(status, 200)
        self.assertIs(data["valid"], True)
        self.assertEqual(data["normalized"]["answers"]["q29"]["partner_attitude"]["status"], "skipped")
        self.assertEqual(list(self.storage_root.glob("*/v*/answers.json")), [])

    def test_submit_read_and_revise_with_fresh_confirmation(self):
        status, first_response, _ = request(self.app, "POST", "/api/submissions", payload())
        self.assertEqual(status, 201)
        self.assertEqual(first_response["action"], "submitted")
        first = first_response["submission"]
        submission_id = first["submission_id"]
        status, read, _ = request(self.app, "GET", f"/api/submissions/{submission_id}/revisions/1")
        self.assertEqual(status, 200)
        read_record = read.get("submission", read)
        self.assertEqual(read_record, first)
        revised = with_answer(payload(), "q01", "age_years", 28)
        status, second, _ = request(self.app, "POST", f"/api/submissions/{submission_id}/revisions", revised)
        self.assertEqual(status, 201)
        self.assertEqual(second["submission"]["revision"], 2)
        self.assertEqual(second["submission"]["answers"]["q01"]["age_years"]["value"], 28)
        del revised["answers"]["q38"]
        status, _, _ = request(self.app, "POST", f"/api/submissions/{submission_id}/revisions", revised)
        self.assertEqual(status, 422)
        self.assertFalse((self.storage_root / submission_id / "v3").exists())

    def test_disagreement_edit_stop_and_pause_never_create_files(self):
        cases = [
            (with_answer(payload(), "consent", "report_use", "disagree"), "stop"),
            (with_answer(payload(), "consent", "private_completion", "uncomfortable"), "pause"),
            (with_answer(payload(), "q38", "report_action", "edit"), "edit"),
            (with_answer(payload(), "q38", "report_action", "stop"), "stop"),
        ]
        for body, action in cases:
            with self.subTest(action=action):
                status, data, _ = request(self.app, "POST", "/api/submissions", body)
                self.assertEqual(status, 200)
                self.assertEqual(data["action"], action)
        self.assertEqual(list(self.storage_root.glob("*/v*/answers.json")), [])

    def test_business_errors_return_422_without_saving(self):
        bad = with_answer(payload(), "q35", "decision_confidence", 11)
        status, data, _ = request(self.app, "POST", "/api/submissions", bad)
        self.assertEqual(status, 422)
        self.assertTrue(data["errors"])
        self.assertEqual(list(self.storage_root.glob("*/v*/answers.json")), [])

    def test_bad_json_utf8_and_content_type_are_client_errors(self):
        for raw in [b"{broken", b"\xff", b'{"x":"\\ud800"}', b'{"x":1,"x":2}', b'{"x":NaN}']:
            with self.subTest(raw=raw):
                status, _, _ = request(self.app, "POST", "/api/validate", raw=raw)
                self.assertEqual(status, 400)
        status, _, _ = request(self.app, "POST", "/api/validate", payload(), content_type="text/plain")
        self.assertEqual(status, 415)

    def test_large_body_unknown_route_and_method(self):
        status, _, _ = request(self.app, "POST", "/api/validate", raw=b"", content_length=str(1024 * 1024 + 1))
        self.assertEqual(status, 413)
        status, _, _ = request(self.app, "GET", "/api/unknown")
        self.assertEqual(status, 404)
        status, _, _ = request(self.app, "DELETE", "/api/questionnaire")
        self.assertEqual(status, 405)

    def test_invalid_identifier_cannot_escape_storage(self):
        for path in ["/api/submissions/not-a-uuid/revisions/1", "/api/submissions/../revisions/1", "/api/submissions/%2e%2e/revisions/1"]:
            with self.subTest(path=path):
                status, _, _ = request(self.app, "GET", path)
                self.assertIn(status, [400, 404])


class RealQuestionnaireIntegrationTests(unittest.TestCase):
    def test_actual_definition_can_validate_submit_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(definition_path=ROOT / "questionnaire" / "definition.json", storage_root=Path(directory))
            status, data, _ = request(app, "GET", "/api/questionnaire")
            self.assertEqual(status, 200)
            numbered = [q["source_number"] for q in data["questions"] if q["source_number"] is not None]
            self.assertEqual(sorted(numbered), [n for n in range(1, 39) if n not in [18, 28]])
            status, created, _ = request(app, "POST", "/api/submissions", payload())
            self.assertEqual(status, 201, created)
            record = created["submission"]
            path = f"/api/submissions/{record['submission_id']}/revisions/1"
            status, loaded, _ = request(app, "GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(loaded.get("submission", loaded), record)


if __name__ == "__main__":
    unittest.main()
