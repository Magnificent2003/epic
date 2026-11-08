"""Immutable local answer revisions; filenames are never supplied by clients."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import UUID, uuid4


class InvalidSubmissionId(ValueError):
    pass


class InvalidRevision(ValueError):
    pass


class SubmissionNotFound(FileNotFoundError):
    pass


class SubmissionStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _submission_path(self, submission_id: str) -> Path:
        try:
            if not isinstance(submission_id, str) or str(UUID(submission_id)) != submission_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise InvalidSubmissionId("Invalid submission identifier") from None
        path = self.root / submission_id
        if path.is_symlink():
            raise InvalidSubmissionId("Submission directory cannot be a symbolic link")
        return path

    def _write(self, directory: Path, normalized: dict, submission_id: str, revision: int) -> dict:
        record = deepcopy(normalized)
        record.update({
            "submission_id": submission_id,
            "revision": revision,
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".answers-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # The directory was reserved by mkdir(exist_ok=False), so this file
            # cannot be another revision writer's destination.
            os.replace(temporary, directory / "answers.json")
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass
            raise
        return record

    def create(self, normalized: dict) -> dict:
        while True:
            submission_id = str(uuid4())
            submission = self._submission_path(submission_id)
            try:
                submission.mkdir(mode=0o700)
            except FileExistsError:
                continue
            break
        revision_dir = submission / "v1"
        try:
            revision_dir.mkdir(mode=0o700)
            return self._write(revision_dir, normalized, submission_id, 1)
        except Exception:
            try:
                submission.rmdir()
            except OSError:
                pass
            raise

    def revise(self, submission_id: str, normalized: dict) -> dict:
        submission = self._submission_path(submission_id)
        if not submission.is_dir() or not (submission / "v1" / "answers.json").is_file():
            raise SubmissionNotFound("Submission not found")
        numbers = [
            int(path.name[1:]) for path in submission.iterdir()
            if re.fullmatch(r"v[1-9][0-9]*", path.name) and path.is_dir()
        ]
        revision = max(numbers, default=0) + 1
        while True:
            directory = submission / f"v{revision}"
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                revision += 1
                continue
            return self._write(directory, normalized, submission_id, revision)

    def read(self, submission_id: str, revision: int) -> dict:
        submission = self._submission_path(submission_id)
        if type(revision) is not int or revision < 1:
            raise InvalidRevision("Revision must be a positive integer")
        directory = submission / f"v{revision}"
        path = directory / "answers.json"
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            raise SubmissionNotFound("Answer revision not found")
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
