"""Render a reviewed report as HTML and PDF, without invoking a model."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.report_pipeline import PipelineError
from backend.report_rendering import export_report
from backend.report_validation import ReportValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description="将审核通过的 final_report.json 确定性排版为同目录 HTML 和 PDF。")
    parser.add_argument("final_report", type=Path)
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire/definition.json")
    args = parser.parse_args()
    try:
        paths = export_report(args.final_report, definition_path=args.definition)
    except PipelineError as exc:
        print(f"{exc.code}：{exc.message}", file=sys.stderr)
        return 2
    except ReportValidationError:
        print("报告未通过结构或证据校验，未生成可用页面。", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("导出失败，请检查文件权限和 PDF 字体配置。", file=sys.stderr)
        return 1
    print(f"HTML：{paths['html_path']}")
    print(f"PDF：{paths['pdf_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
