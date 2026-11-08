import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.storage import (
    InvalidRevision,
    InvalidSubmissionId,
    SubmissionNotFound,
    SubmissionStore,
)
from backend.validation import normalize_submission
from tests.fixtures import definition, payload, with_answer


class LocalSubmissionStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = SubmissionStore(self.root)
        self.body = normalize_submission(payload(), definition())

    def test_create_and_read_preserve_json_and_server_metadata(self):
        record = self.store.create(self.body)
        self.assertEqual(str(uuid.UUID(record["submission_id"])), record["submission_id"])
        self.assertEqual(record["revision"], 1)
        self.assertTrue(record["submitted_at"])
        saved_path = self.root / record["submission_id"] / "v1" / "answers.json"
        self.assertEqual(json.loads(saved_path.read_text(encoding="utf-8")), record)
        self.assertEqual(self.store.read(record["submission_id"], 1), record)

    def test_revision_keeps_previous_answers_immutable(self):
        first = self.store.create(self.body)
        updated = normalize_submission(with_answer(payload(), "q01", "age_years", 28), definition())
        second = self.store.revise(first["submission_id"], updated)
        self.assertEqual(second["submission_id"], first["submission_id"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["answers"]["q01"]["age_years"]["value"], 28)
        self.assertEqual(self.store.read(first["submission_id"], 1), first)
        self.assertEqual(self.store.read(first["submission_id"], 2), second)

    def test_concurrent_revisions_have_distinct_complete_files(self):
        first = self.store.create(self.body)
        submission_id = first["submission_id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: self.store.revise(submission_id, self.body), range(8)))
        self.assertEqual(sorted(record["revision"] for record in records), list(range(2, 10)))
        for record in records:
            self.assertEqual(self.store.read(submission_id, record["revision"]), record)

    def test_untrusted_path_and_revision_are_rejected(self):
        for submission_id in ["../escape", "/tmp/outside", "not-a-uuid", "a/../../b", ""]:
            with self.subTest(submission_id=submission_id), self.assertRaises(InvalidSubmissionId):
                self.store.read(submission_id, 1)
        first = self.store.create(self.body)
        for revision in [0, -1, "../v1", True, 1.5]:
            with self.subTest(revision=revision), self.assertRaises(InvalidRevision):
                self.store.read(first["submission_id"], revision)

    def test_missing_submission_or_revision_is_not_created(self):
        missing_id = str(uuid.uuid4())
        with self.assertRaises(SubmissionNotFound):
            self.store.read(missing_id, 1)
        with self.assertRaises(SubmissionNotFound):
            self.store.revise(missing_id, self.body)
        self.assertFalse((self.root / missing_id).exists())
        first = self.store.create(self.body)
        with self.assertRaises(SubmissionNotFound):
            self.store.read(first["submission_id"], 2)


if __name__ == "__main__":
    unittest.main()
