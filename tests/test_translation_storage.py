"""Integration coverage for immutable answer revisions and derived model input."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.storage import SubmissionStore
from backend.translation import OUTPUT_FILENAME, translate_file, write_translation
from backend.validation import ValidationError, normalize_submission
from tests.fixtures import payload, with_answer


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TEXT = "仅供测试的私人补充内容，不应出现在日志中"


class TranslationStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.definition = json.loads((ROOT / "questionnaire/definition.json").read_text(encoding="utf-8"))
        self.store = SubmissionStore(self.root / "submissions", self.definition)

    def normalized(self, body=None):
        return normalize_submission(payload() if body is None else body, self.definition)

    def revision_dir(self, record):
        return self.store.root / record["submission_id"] / f"v{record['revision']}"

    def write_existing(self, body):
        path = self.root / "existing" / "answers.json"
        path.parent.mkdir(exist_ok=True)
        # Preserve distinctive whitespace to detect a read/serialize rewrite.
        path.write_text(json.dumps(body, ensure_ascii=False, indent=4) + "\n\n", encoding="utf-8")
        return path

    def run_cli(self, path):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/translate_answers.py"), str(path)],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )

    def test_creation_and_revision_write_matching_artifacts_without_changing_v1(self):
        first = self.store.create(self.normalized(with_answer(payload(), "q01", "age_years", 26)))
        first_dir = self.revision_dir(first)
        first_bytes = {name: (first_dir / name).read_bytes() for name in ["answers.json", OUTPUT_FILENAME]}
        first_input = json.loads(first_bytes[OUTPUT_FILENAME])
        self.assertEqual(first_input["status"], "ready")
        self.assertIn("[q01.age_years] 年龄｜26岁", first_input["lines"])

        second = self.store.revise(first["submission_id"], self.normalized(with_answer(payload(), "q01", "age_years", 27)))
        self.assertEqual(second["revision"], 2)
        second_input = json.loads((self.revision_dir(second) / OUTPUT_FILENAME).read_text(encoding="utf-8"))
        self.assertIn("[q01.age_years] 年龄｜27岁", second_input["lines"])
        for name, before in first_bytes.items():
            self.assertEqual((first_dir / name).read_bytes(), before)
        self.assertEqual(self.store.read(first["submission_id"], 1), first)
        self.assertEqual(self.store.read(first["submission_id"], 2), second)

    def test_unresolved_exclusion_preserves_answers_but_blocks_model_input(self):
        body = with_answer(payload(), "q38", "exclusion_notes", PRIVATE_TEXT)
        record = self.store.create(self.normalized(body))
        directory = self.revision_dir(record)
        saved = json.loads((directory / "answers.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["answers"]["q38"]["exclusion_notes"]["value"], PRIVATE_TEXT)
        artifact_text = (directory / OUTPUT_FILENAME).read_text(encoding="utf-8")
        artifact = json.loads(artifact_text)
        self.assertEqual(artifact["status"], "blocked")
        self.assertTrue(artifact["issues"])
        self.assertNotIn("lines", artifact)
        self.assertNotIn("instructions", artifact)
        self.assertNotIn(PRIVATE_TEXT, artifact_text)

    def test_failed_translation_write_rolls_back_new_submission_and_allows_retry(self):
        for publish_first in [False, True]:
            with self.subTest(publish_first=publish_first):
                store = SubmissionStore(self.root / f"failure-{publish_first}", self.definition)

                def fail(directory, translation):
                    if publish_first:
                        write_translation(directory, translation)
                    raise OSError("Simulated derived-file write failure")

                with patch("backend.storage.write_translation", side_effect=fail), self.assertRaises(OSError):
                    store.create(self.normalized())
                self.assertEqual(list(store.root.iterdir()), [])
                record = store.create(self.normalized())
                directory = store.root / record["submission_id"] / "v1"
                self.assertTrue((directory / "answers.json").is_file())
                self.assertTrue((directory / OUTPUT_FILENAME).is_file())

    def test_failed_revision_has_no_leftovers_and_retry_reuses_next_revision(self):
        first = self.store.create(self.normalized())
        first_dir = self.revision_dir(first)
        original = {file.name: file.read_bytes() for file in first_dir.iterdir()}
        with patch("backend.storage.write_translation", side_effect=OSError("Write failed")), self.assertRaises(OSError):
            self.store.revise(first["submission_id"], self.normalized())
        self.assertEqual([path.name for path in first_dir.parent.iterdir()], ["v1"])
        self.assertEqual({file.name: file.read_bytes() for file in first_dir.iterdir()}, original)
        second = self.store.revise(first["submission_id"], self.normalized())
        self.assertEqual(second["revision"], 2)
        self.assertEqual({file.name for file in self.revision_dir(second).iterdir()}, {"answers.json", OUTPUT_FILENAME})

    def test_backfill_preserves_original_bytes_and_repeated_output_is_stable(self):
        body = with_answer(payload(), "q01", "age_years", 26)
        path = self.write_existing(body)
        before = path.read_bytes()
        first = translate_file(path, self.definition)
        first_bytes = (path.parent / OUTPUT_FILENAME).read_bytes()
        second = translate_file(path, self.definition)
        self.assertEqual(first["status"], "ready")
        self.assertEqual(first, second)
        self.assertEqual((path.parent / OUTPUT_FILENAME).read_bytes(), first_bytes)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_existing_answers_replace_stale_ready_artifact_with_blocked(self):
        invalid_bodies = [
            json.dumps(with_answer(payload(), "q01", "age_years", PRIVATE_TEXT), ensure_ascii=False),
            '{"answers": "' + PRIVATE_TEXT + '",',
        ]
        for invalid_body in invalid_bodies:
            with self.subTest(invalid_body=invalid_body[:12]):
                path = self.write_existing(payload())
                self.assertEqual(translate_file(path, self.definition)["status"], "ready")
                path.write_text(invalid_body, encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaises(ValidationError):
                    translate_file(path, self.definition)
                self.assertEqual(path.read_bytes(), before)
                artifact_text = (path.parent / OUTPUT_FILENAME).read_text(encoding="utf-8")
                artifact = json.loads(artifact_text)
                self.assertEqual(artifact["status"], "blocked")
                self.assertNotIn("lines", artifact)
                self.assertNotIn("instructions", artifact)
                self.assertNotIn(PRIVATE_TEXT, artifact_text)

    def test_missing_source_invalidates_previous_ready_artifact(self):
        path = self.write_existing(payload())
        self.assertEqual(translate_file(path, self.definition)["status"], "ready")
        path.unlink()
        with self.assertRaises(OSError):
            translate_file(path, self.definition)
        artifact = json.loads((path.parent / OUTPUT_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "blocked")
        self.assertNotIn("lines", artifact)
        self.assertEqual(artifact["issues"][0]["code"], "source_unavailable")

    def test_ready_artifact_is_published_only_after_answers_exist(self):
        def check_source(directory, translation):
            source = json.loads((directory / "answers.json").read_text(encoding="utf-8"))
            self.assertEqual(source["revision"], 1)
            return write_translation(directory, translation)

        with patch("backend.storage.write_translation", side_effect=check_source):
            self.store.create(self.normalized())

    def test_cli_runs_from_another_directory_without_printing_answers(self):
        path = self.write_existing(with_answer(payload(), "q38", "report_notes", PRIVATE_TEXT))
        before = path.read_bytes()
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(path.parent / OUTPUT_FILENAME), result.stdout)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        artifact = json.loads((path.parent / OUTPUT_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "ready")
        self.assertTrue(any(PRIVATE_TEXT in line for line in artifact["lines"]))
        self.assertEqual(path.read_bytes(), before)

    def test_cli_blocked_exit_is_two_and_does_not_print_exclusion_text(self):
        path = self.write_existing(with_answer(payload(), "q38", "exclusion_notes", PRIVATE_TEXT))
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        artifact = json.loads((path.parent / OUTPUT_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(artifact["status"], "blocked")

    def test_cli_validation_and_io_failures_do_not_disclose_answer_values(self):
        path = self.write_existing(with_answer(payload(), "q01", "age_years", PRIVATE_TEXT))
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        path.unlink()
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(PRIVATE_TEXT, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
