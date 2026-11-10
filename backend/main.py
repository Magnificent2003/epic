"""Local questionnaire API and asynchronous, reviewed report delivery."""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
import re
from typing import Callable

from .storage import InvalidRevision, InvalidSubmissionId, SubmissionNotFound, SubmissionStore
from .validation import ValidationError, field_index, normalize_submission, submit_action
from .report_jobs import ReportJobs, ReportNotReady


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_BODY_BYTES = 1024 * 1024
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/questionnaire.js": ("questionnaire.js", "text/javascript; charset=utf-8"),
}


class RequestError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object member")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


class QuestionnaireApp:
    def __init__(self, definition_path: str | Path | None = None, storage_root: str | Path | None = None, *, auto_reports: bool = True, report_runner=None, report_renderer=None):
        path = Path(definition_path) if definition_path is not None else PROJECT_ROOT / "questionnaire" / "definition.json"
        with path.open(encoding="utf-8") as handle:
            self.definition = json.load(handle)
        field_index(self.definition)
        self.store = SubmissionStore(storage_root if storage_root is not None else PROJECT_ROOT / "work_dir", self.definition)
        self.auto_reports = auto_reports
        self.jobs = ReportJobs(self.store, path, runner=report_runner, renderer=report_renderer)

    def close(self, wait: bool = True) -> None:
        self.jobs.close(wait=wait)

    @staticmethod
    def _revision(value: str) -> int:
        if not re.fullmatch(r"[1-9][0-9]*", value) or len(value) > 10:
            raise InvalidRevision("Invalid revision")
        return int(value)

    def _read_json(self, environ: dict) -> dict:
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
            raise RequestError(415, "content_type", "请使用application/json请求格式。")
        length_text = environ.get("CONTENT_LENGTH", "")
        if not length_text:
            raise RequestError(411, "content_length", "请求须提供Content-Length。")
        try:
            length = int(length_text)
        except ValueError:
            raise RequestError(400, "content_length", "请求长度无效。") from None
        if length < 0:
            raise RequestError(400, "content_length", "请求长度无效。")
        if length > MAX_BODY_BYTES:
            raise RequestError(413, "body_too_large", "请求超过1MiB限制。")
        raw = environ["wsgi.input"].read(length)
        if len(raw) != length:
            raise RequestError(400, "incomplete_body", "请求内容不完整。")
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            # JSON escape sequences can contain lone surrogate characters even
            # when the incoming bytes themselves are valid UTF-8.
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            return payload
        except (UnicodeError, ValueError, RecursionError):
            raise RequestError(400, "invalid_json", "请求须包含有效UTF-8 JSON。") from None

    def _route(self, environ: dict) -> tuple[int, dict]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if path == "/api/health":
            if method != "GET":
                raise RequestError(405, "method", "该接口仅接受GET。")
            return 200, {"status": "ok", "questionnaire_version": self.definition["version"], "report_generation": "background"}
        if path == "/api/questionnaire":
            if method != "GET":
                raise RequestError(405, "method", "该接口仅接受GET。")
            return 200, self.definition
        report_match = re.fullmatch(r"/api/submissions/([^/]+)/revisions/([^/]+)/report", path)
        if report_match:
            submission_id, revision = report_match.groups()
            revision = self._revision(revision)
            if method == "GET":
                return 200, self.jobs.status(submission_id, revision)
            if method == "POST":
                state = self.jobs.start(submission_id, revision)
                return (202 if state["status"] in {"queued", "generating", "reviewing", "rendering"} else 200), state
            raise RequestError(405, "method", "该接口仅接受GET或POST。")
        revision_match = re.fullmatch(r"/api/submissions/([^/]+)/revisions/([^/]+)", path)
        update_match = re.fullmatch(r"/api/submissions/([^/]+)/revisions", path)
        if revision_match:
            if method != "GET":
                raise RequestError(405, "method", "该接口仅接受GET。")
            submission_id, revision = revision_match.groups()
            return 200, {"submission": self.store.read(submission_id, self._revision(revision))}
        if path not in {"/api/validate", "/api/submissions"} and not update_match:
            raise RequestError(404, "not_found", "接口不存在。")
        if method != "POST":
            raise RequestError(405, "method", "该接口仅接受POST。")
        payload = self._read_json(environ)
        normalized = normalize_submission(payload, self.definition)
        if path == "/api/validate":
            return 200, {"valid": True, "normalized": normalized}
        action = submit_action(normalized, self.definition)
        if action != "submit":
            return 200, {"action": action}
        if update_match:
            record = self.store.revise(update_match.group(1), normalized)
        else:
            record = self.store.create(normalized)
        try:
            report = (self.jobs.start if self.auto_reports else self.jobs.status)(record["submission_id"], record["revision"])
        except Exception:
            # Saving succeeded; a report scheduling failure must not encourage
            # duplicate questionnaire submissions.
            report = {"status": "failed", "message": "答卷已保存，报告暂未启动，请稍后重试。", "retryable": True,
                      "status_url": f"/api/submissions/{record['submission_id']}/revisions/{record['revision']}/report"}
        return 201, {"action": "submitted", "submission": record, "report": report}

    def __call__(self, environ: dict, start_response: Callable):
        try:
            report_asset = re.fullmatch(r"/api/submissions/([^/]+)/revisions/([^/]+)/(report\.(?:html|pdf))", environ.get("PATH_INFO", "/"))
            if report_asset:
                method = environ.get("REQUEST_METHOD", "GET").upper()
                if method not in {"GET", "HEAD"}:
                    raise RequestError(405, "method", "该文件仅接受GET或HEAD。")
                submission_id, revision_text, filename = report_asset.groups()
                revision = self._revision(revision_text)
                body = self.jobs.asset(submission_id, revision, filename)
                pdf = filename.endswith(".pdf")
                headers = [
                    ("Content-Type", "application/pdf" if pdf else "text/html; charset=utf-8"),
                    ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                ]
                if pdf:
                    headers.append(("Content-Disposition", f'attachment; filename="EPIC-report-v{revision}.pdf"'))
                else:
                    headers.append(("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; frame-ancestors 'self'"))
                start_response("200 OK", headers)
                return [b"" if method == "HEAD" else body]
            asset = STATIC_FILES.get(environ.get("PATH_INFO", "/"))
            if asset:
                method = environ.get("REQUEST_METHOD", "GET").upper()
                if method not in {"GET", "HEAD"}:
                    raise RequestError(405, "method", "该页面仅接受GET或HEAD。")
                filename, content_type = asset
                path = PROJECT_ROOT / "app" / filename
                if not path.is_file():
                    raise RequestError(404, "not_found", "页面资源不存在。")
                body = path.read_bytes()
                start_response("200 OK", [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(body))),
                    ("Cache-Control", "no-store"),
                    ("X-Content-Type-Options", "nosniff"),
                ])
                return [b"" if method == "HEAD" else body]
            status, result = self._route(environ)
        except ValidationError as exc:
            status, result = 422, {"valid": False, "errors": exc.errors}
        except RequestError as exc:
            status, result = exc.status, {"error": {"code": exc.code, "message": exc.message}}
        except (InvalidSubmissionId, InvalidRevision):
            status, result = 400, {"error": {"code": "invalid_identifier", "message": "答卷标识或修订号无效。"}}
        except SubmissionNotFound:
            status, result = 404, {"error": {"code": "not_found", "message": "答卷版本不存在。"}}
        except ReportNotReady:
            status, result = 409, {"error": {"code": "report_not_ready", "message": "报告尚不可查看或下载，请先查询处理状态。"}}
        except Exception:
            # Never include exception text, tracebacks, raw answers or secrets.
            status, result = 500, {"error": {"code": "internal_error", "message": "处理失败，请稍后重试。"}}
        body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if status == 405:
            message = result.get("error", {}).get("message", "")
            headers.append(("Allow", "GET, HEAD" if "HEAD" in message else "GET, POST" if "GET或POST" in message else "GET" if "GET" in message else "POST"))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [body]


def create_app(definition_path: str | Path | None = None, storage_root: str | Path | None = None, *, auto_reports: bool = True, report_runner=None, report_renderer=None) -> QuestionnaireApp:
    return QuestionnaireApp(definition_path=definition_path, storage_root=storage_root, auto_reports=auto_reports, report_runner=report_runner, report_renderer=report_renderer)
