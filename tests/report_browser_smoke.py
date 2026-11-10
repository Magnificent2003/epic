"""Optional browser checks for submission -> report -> real PDF download.

Requires Playwright/Chromium and the project's PDF dependencies. All model
responses and answers are synthetic; no external model request is made.
    python3 tests/report_browser_smoke.py --screenshots /tmp/epic-report-preview
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import expect, sync_playwright
    import reportlab  # noqa: F401
except ImportError:
    raise SystemExit("Install the project PDF dependencies and optional Playwright/Chromium before running this browser check.")

from backend.main import create_app
from tests.fixtures import payload, with_answer
from tests.test_report_jobs import Runner


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Local EPIC report browser verification with simulated models")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    passed = []

    def check(name):
        passed.append(name)
        print("PASS", name, flush=True)

    entered, release = threading.Event(), threading.Event()
    runner = Runner("pass", "fail", "pass", "hold", entered=entered, release=release)
    with tempfile.TemporaryDirectory(prefix="epic-report-browser-") as temporary:
        screenshots = args.screenshots or Path(temporary) / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)
        app = create_app(storage_root=Path(temporary) / "answers", report_runner=runner)
        server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--no-proxy-server"])
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(12000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                page.locator('input[name="consent.report_use"][value="agree"]').check()
                page.locator('input[name="consent.private_completion"][value="comfortable"]').check()
                page.locator("#next-step").click()
                page.locator('input[name="q01.age_years"]').fill("26")
                page.locator(".step-button").nth(7).click()
                page.locator('input[name="q34.current_preference"][value="undecided"]').check()
                page.locator("#next-step").click()
                page.locator('input[name="q38.report_action"][value="confirm"]').check()
                page.get_by_role("button", name="提交并生成报告", exact=False).click()
                expect(page.get_by_role("heading", name=re.compile("报告正在准备中|正在为您整理报告"))).to_be_visible()
                assert entered.wait(2)
                assert page.get_by_role("button", name="下载答卷", exact=True).count() == 0
                assert page.get_by_role("link", name="查看我的报告", exact=False).count() == 0
                first_location = page.url
                assert "#submission=" in first_location and "&revision=1" in first_location
                assert context.request.get(base + "/api/health").ok
                page.screenshot(path=str(screenshots / "waiting-desktop.png"), full_page=True)
                page.reload()
                expect(page.get_by_role("heading", name=re.compile("报告正在准备中|正在为您整理报告"))).to_be_visible()
                assert runner.calls == 1
                check("submission starts background work; waiting state replaces answer download; refresh resumes without duplicate generation")

                release.set()
                expect(page.get_by_role("heading", name="您的报告已准备好", exact=True)).to_be_visible(timeout=20000)
                expect(page.get_by_role("link", name="下载 PDF", exact=True)).to_be_visible()
                page.screenshot(path=str(screenshots / "ready-desktop.png"), full_page=True)
                with page.expect_popup() as opened:
                    page.get_by_role("link", name="查看我的报告", exact=False).click()
                report_page = opened.value
                report_page.wait_for_load_state()
                expect(report_page.get_by_role("heading", name="您目前的倾向", exact=True)).to_be_visible()
                expect(report_page.get_by_role("heading", name="两种选择分别对您意味着什么", exact=True)).to_be_visible()
                expect(report_page.get_by_role("heading", name="综合分析", exact=True)).to_be_visible()
                expect(report_page.get_by_role("heading", name="关于是否继续妊娠的建议", exact=True)).to_be_visible()
                assert not report_page.evaluate("document.documentElement.scrollWidth > innerWidth")
                report_page.screenshot(path=str(screenshots / "report-desktop.png"), full_page=True)
                with page.expect_download() as downloaded:
                    page.get_by_role("link", name="下载 PDF", exact=True).click()
                pdf_path = Path(downloaded.value.path())
                assert pdf_path.read_bytes().startswith(b"%PDF-")
                if shutil.which("pdftotext"):
                    extracted = subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, check=True).stdout
                    assert "您目前尚无明确倾向或两边摇摆" in extracted
                check("approved report opens as four-section HTML and downloads a real PDF with readable Chinese")

                page.set_viewport_size({"width": 390, "height": 844})
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.screenshot(path=str(screenshots / "ready-mobile.png"), full_page=True)
                report_page.set_viewport_size({"width": 390, "height": 844})
                assert not report_page.evaluate("document.documentElement.scrollWidth > innerWidth")
                report_page.screenshot(path=str(screenshots / "report-mobile.png"), full_page=True)
                report_page.close()
                check("waiting/ready report actions and standalone report remain usable on a 390px screen")

                body = with_answer(payload(), "q34", "current_preference", "undecided")
                response = context.request.post(base + "/api/submissions", data=body)
                assert response.status == 201
                failed_record = response.json()["submission"]
                failed_link = f"{base}/?case=failed#submission={failed_record['submission_id']}&revision=1"
                page.goto(failed_link)
                expect(page.get_by_role("heading", name="报告暂未生成", exact=True)).to_be_visible()
                assert page.get_by_role("link", name="查看我的报告", exact=False).count() == 0
                page.get_by_role("button", name="重新生成报告", exact=True).click()
                expect(page.get_by_role("heading", name="您的报告已准备好", exact=True)).to_be_visible(timeout=20000)
                assert runner.calls == 3
                check("failed model review offers an explicit retry and does not publish the unreviewed draft")

                response = context.request.post(base + "/api/submissions", data=body)
                held = response.json()["submission"]
                page.goto(f"{base}/?case=hold#submission={held['submission_id']}&revision=1")
                expect(page.get_by_role("heading", name="报告暂缓提供", exact=True)).to_be_visible()
                assert page.get_by_role("link", name="查看我的报告", exact=False).count() == 0
                assert page.get_by_role("link", name="下载 PDF", exact=True).count() == 0
                assert page.get_by_role("button", name="重新生成报告", exact=True).count() == 0
                prefix = f"{base}/api/submissions/{held['submission_id']}/revisions/1"
                assert context.request.get(prefix + "/report.html").status != 200
                assert context.request.get(prefix + "/report.pdf").status != 200
                page.screenshot(path=str(screenshots / "held-mobile.png"), full_page=True)
                check("hold remains unavailable and offers no automatic regeneration or download")

                blocked_body = with_answer(body, "q38", "exclusion_notes", "请先核对这段排除要求")
                response = context.request.post(base + "/api/submissions", data=blocked_body)
                blocked = response.json()["submission"]
                page.goto(f"{base}/?case=blocked#submission={blocked['submission_id']}&revision=1")
                expect(page.get_by_role("heading", name="需要先核对报告使用范围", exact=True)).to_be_visible()
                assert runner.calls == 4
                check("unresolved report exclusions block generation without invoking the scripted model")

                failed_once = [False]
                def fail_first_query(route):
                    if not failed_once[0] and route.request.method == "GET":
                        failed_once[0] = True
                        route.abort()
                    else:
                        route.continue_()
                page.route("**/report", fail_first_query)
                page.goto(first_location.replace("/#", "/?case=network#"))
                expect(page.get_by_role("button", name="恢复查询", exact=True)).to_be_visible()
                page.get_by_role("button", name="恢复查询", exact=True).click()
                expect(page.get_by_role("heading", name="您的报告已准备好", exact=True)).to_be_visible()
                assert runner.calls == 4
                page.unroute("**/report", fail_first_query)
                check("network recovery queries existing progress without submitting or generating again")

                page.get_by_role("button", name="返回修改", exact=True).click()
                expect(page.get_by_role("heading", name="给自己的回答，再看一眼", exact=True)).to_be_visible()
                assert "#submission=" not in page.url
                assert not page.locator('input[name="q38.report_action"][value="confirm"]').is_checked()
                assert not errors, errors
                check("return to answers removes the report link and requires fresh confirmation; no uncaught JavaScript errors")

                page.locator('input[name="q38.report_action"][value="confirm"]').check()
                page.get_by_role("button", name="提交并生成报告", exact=False).click()
                expect(page.get_by_role("heading", name="报告暂缓提供", exact=True)).to_be_visible()
                assert "&revision=2" in page.url
                assert page.get_by_role("link", name="查看我的报告", exact=False).count() == 0
                assert page.get_by_role("link", name="下载 PDF", exact=True).count() == 0
                first_id = re.search(r"submission=([^&]+)", first_location).group(1)
                old_status = context.request.get(f"{base}/api/submissions/{first_id}/revisions/1/report").json()
                assert old_status["status"] == "ready"
                assert runner.calls == 5
                assert not errors, errors
                check("resubmission creates version two and never reuses the ready report of version one")
                context.close()
                browser.close()
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.close()
    print(f"Completed {len(passed)} report browser scenarios.", flush=True)


if __name__ == "__main__":
    main()
