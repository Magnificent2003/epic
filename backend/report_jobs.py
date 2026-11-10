"""Local background report jobs, with version-bound progress and publication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from .report_pipeline import PipelineError, _now, _write_json, prepare_case, run_report
from .storage import SubmissionStore


ACTIVE = {"queued", "generating", "reviewing", "rendering"}
MESSAGES = {
    "not_started": "答卷已保存，可以开始生成个人支持报告。",
    "queued": "答卷已提交，报告正在等待处理，请耐心等待几分钟。",
    "generating": "智能体正在整理与分析您的回答，请耐心等待几分钟。",
    "reviewing": "报告初稿已生成，正在核对依据与表述。",
    "rendering": "报告已通过审核，正在排版并准备PDF。",
    "ready": "您的个人支持报告已准备好，可以查看或下载。",
    "hold": "本次报告需要进一步核对，暂时不能展示。您可以返回检查或补充回答。",
    "blocked": "本次报告暂不能生成，请返回核对授权与报告使用范围。",
    "failed": "暂未完成报告生成，您的答卷已保存，可以稍后重试。",
    "interrupted": "报告处理已中断，您的答卷已保存，可以重新开始生成。",
}
ARTIFACTS = ("report.html", "report.pdf")


class ReportNotReady(ValueError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_atomic(source: Path, destination: Path) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".publish-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ReportJobs:
    def __init__(self, store: SubmissionStore, definition_path: str | Path, *, runner=None, renderer=None):
        self.store = store
        self.definition_path = Path(definition_path)
        self.runner = runner or run_report
        self.renderer = renderer
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="epic-report")
        self._lock = RLock()
        self._futures = {}
        self._closed = False

    def _directory(self, submission_id: str, revision: int) -> Path:
        # Reuse storage's UUID, revision, existence and symlink checks.
        self.store.read(submission_id, revision)
        return self.store.root / submission_id / f"v{revision}"

    def _write_state(self, directory: Path, status: str, **extra) -> dict:
        state = {"status": status, "updated_at": _now(), **extra}
        _write_json(directory / "report_status.json", state)
        return state

    def _read_state(self, directory: Path) -> dict:
        path = directory / "report_status.json"
        if not path.exists():
            return {"status": "not_started"}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("status") not in MESSAGES:
                raise ValueError
            return state
        except (OSError, ValueError):
            return {"status": "failed"}

    def _validate_ready(self, directory: Path, state: dict) -> None:
        from .report_rendering import load_approved_report
        load_approved_report(directory / "final_report.json", self.definition_path)
        for name in ARTIFACTS:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ReportNotReady("Report artifact missing")
            expected = state.get("artifacts", {}).get(name)
            if expected and _digest(path) != expected:
                raise ReportNotReady("Report artifact changed")
        expected = state.get("final_sha256")
        if expected and _digest(directory / "final_report.json") != expected:
            raise ReportNotReady("Final report changed")

    @staticmethod
    def _public(submission_id: str, revision: int, state: dict) -> dict:
        status = state["status"]
        base = f"/api/submissions/{submission_id}/revisions/{revision}"
        result = {
            "status": status, "message": MESSAGES[status],
            "retryable": status in {"not_started", "failed", "interrupted"},
            "status_url": f"{base}/report",
        }
        if status == "ready":
            result.update(report_url=f"{base}/report.html", pdf_url=f"{base}/report.pdf")
        return result

    def status(self, submission_id: str, revision: int) -> dict:
        directory = self._directory(submission_id, revision)
        key = (submission_id, revision)
        with self._lock:
            state = self._read_state(directory)
            if state["status"] in ACTIVE and key not in self._futures:
                state = self._write_state(directory, "interrupted")
            # Also recognize an approved report exported by the command line.
            if state["status"] == "not_started" and (directory / "run.json").is_file():
                try:
                    self._validate_ready(directory, state)
                    state = self._write_state(directory, "ready", artifacts={n: _digest(directory / n) for n in ARTIFACTS}, final_sha256=_digest(directory / "final_report.json"))
                except Exception:
                    pass
            if state["status"] == "ready":
                try:
                    self._validate_ready(directory, state)
                except Exception:
                    state = self._write_state(directory, "blocked")
            return self._public(submission_id, revision, state)

    def start(self, submission_id: str, revision: int) -> dict:
        directory = self._directory(submission_id, revision)
        key = (submission_id, revision)
        with self._lock:
            current = self.status(submission_id, revision)
            if key in self._futures or current["status"] in ACTIVE or not current["retryable"]:
                return current
            if self._closed:
                raise ReportNotReady("Report service closed")
            self._write_state(directory, "queued")
            try:
                self._futures[key] = self._executor.submit(self._work, key, directory)
            except Exception:
                self._write_state(directory, "failed")
                raise
            return self._public(submission_id, revision, {"status": "queued"})

    def _progress(self, directory: Path, status: str) -> None:
        if status not in {"generating", "reviewing", "rendering"}:
            return
        with self._lock:
            self._write_state(directory, status)

    def _work(self, key: tuple, directory: Path) -> None:
        try:
            case = prepare_case(directory / "model_input.json", self.definition_path)
            self._progress(directory, "generating")
            result = self.runner(directory / "model_input.json", definition_path=self.definition_path, progress=lambda status: self._progress(directory, status))
            case.check_unchanged()
            if result["status"] != "approved":
                terminal = "hold" if result["status"] == "hold" else "failed"
                with self._lock:
                    self._write_state(directory, terminal)
                return
            run_directory = Path(result["run_dir"]).resolve()
            if run_directory.parent != (directory / "reports").resolve():
                raise ReportNotReady("Unexpected report run location")
            self._progress(directory, "rendering")
            if self.renderer is None:
                from .report_rendering import export_report
                renderer = export_report
            else:
                renderer = self.renderer
            artifacts = renderer(run_directory / "final_report.json", definition_path=self.definition_path)
            case.check_unchanged()
            for name in ("draft_report.json", "review_report.json", "final_report.json"):
                _copy_atomic(run_directory / name, directory / name)
            _copy_atomic(Path(artifacts["html_path"]), directory / "report.html")
            _copy_atomic(Path(artifacts["pdf_path"]), directory / "report.pdf")
            _copy_atomic(run_directory / "run.json", directory / "run.json")
            case.check_unchanged()
            state = {"artifacts": {n: _digest(directory / n) for n in ARTIFACTS}, "final_sha256": _digest(directory / "final_report.json")}
            self._validate_ready(directory, state)
            with self._lock:
                self._write_state(directory, "ready", **state)
        except PipelineError as exc:
            status = "blocked" if exc.code in {"input_blocked", "invalid_answers", "stale_translation", "input_changed"} else "failed"
            with self._lock:
                self._write_state(directory, status)
        except Exception:
            # Never publish model errors, reviewer reasons or answer content.
            with self._lock:
                self._write_state(directory, "failed")
        finally:
            with self._lock:
                self._futures.pop(key, None)

    def asset(self, submission_id: str, revision: int, filename: str) -> bytes:
        if filename not in ARTIFACTS:
            raise ReportNotReady("Unknown report artifact")
        with self._lock:
            if self.status(submission_id, revision)["status"] != "ready":
                raise ReportNotReady("Report not ready")
            directory = self._directory(submission_id, revision)
            return (directory / filename).read_bytes()

    def close(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait)
