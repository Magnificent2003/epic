"""The questionnaire and its assets share one origin with the API."""

import io
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from backend.main import create_app


class QuestionnairePageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = create_app(storage_root=Path(directory.name))

    def request(self, path, method="GET"):
        environ = {}
        setup_testing_defaults(environ)
        environ.update(REQUEST_METHOD=method, PATH_INFO=path)
        environ["wsgi.input"] = io.BytesIO()
        response = {}

        def start_response(status, headers):
            response["status"] = int(status.split()[0])
            response["headers"] = dict(headers)

        response["body"] = b"".join(self.app(environ, start_response))
        return response

    def test_page_and_modules_are_served_with_correct_types(self):
        for path, mime in {
            "/": "text/html",
            "/assets/styles.css": "text/css",
            "/assets/app.js": "text/javascript",
            "/assets/questionnaire.js": "text/javascript",
        }.items():
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response["status"], 200)
                self.assertTrue(response["headers"]["Content-Type"].startswith(mime))
                self.assertGreater(len(response["body"]), 0)
                self.assertEqual(response["headers"]["Cache-Control"], "no-store")
        self.assertIn(b'lang="zh-CN"', self.request("/")["body"])

    def test_head_and_disallowed_methods(self):
        full = self.request("/")
        head = self.request("/", "HEAD")
        self.assertEqual(head["status"], 200)
        self.assertEqual(head["body"], b"")
        self.assertEqual(head["headers"]["Content-Length"], full["headers"]["Content-Length"])
        post = self.request("/", "POST")
        self.assertEqual(post["status"], 405)
        self.assertEqual(post["headers"]["Allow"], "GET, HEAD")

    def test_static_routes_do_not_expose_project_or_answer_files(self):
        for path in ["/.env", "/backend/main.py", "/work_dir/answers.json", "/assets/../.env", "/assets/%2e%2e/.env"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)["status"], 404)


if __name__ == "__main__":
    unittest.main()
