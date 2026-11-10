#!/usr/bin/env python3
"""One-click offline export of every saved answer revision under work_dir."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# IDE "Run" and direct execution may select the system Python. Reuse this
# project's installed environment instead of making the user pass arguments.
if __name__ == "__main__" and importlib.util.find_spec("reportlab") is None:
    environment = PROJECT_ROOT / ".venv"
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if interpreter.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        os.execv(str(interpreter), [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]])
    print("缺少 PDF 依赖：请先用项目 Python 安装 requirements.txt。", file=sys.stderr)
    raise SystemExit(1)

from backend.questionnaire_pdf import OUTPUT_FILENAME, export_questionnaire
from backend.report_validation import parse_model_json
from backend.validation import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description="递归导出 work_dir 内的原始问卷 PDF；无需参数，不调用大模型，已有文件默认跳过。")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "work_dir", help="默认使用项目下的 work_dir，不受当前工作目录影响")
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire/definition.json")
    parser.add_argument("--force", action="store_true", help="明确要求重新生成已有 questionnaire.pdf")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if not root.is_dir():
            raise ValueError("答卷目录不存在。")
        definition = parse_model_json(args.definition.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"无法开始导出：{exc}", file=sys.stderr)
        return 1
    generated = skipped = failed = 0
    sources = sorted(root.rglob("answers.json"))
    for source in sources:
        relative = source.relative_to(root)
        try:
            if source.is_symlink() or not source.resolve().is_relative_to(root):
                raise ValueError("不导出符号链接或目录外的答卷。")
            if export_questionnaire(source, definition, force=args.force):
                generated += 1
                print(f"已生成：{relative.with_name(OUTPUT_FILENAME)}")
            else:
                skipped += 1
                print(f"已跳过：{relative.with_name(OUTPUT_FILENAME)}")
        except ValidationError as exc:
            failed += 1
            fields = "、".join(issue["field"] for issue in exc.errors)
            print(f"失败：{relative}；答卷校验未通过：{fields}", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(f"失败：{relative}；{type(exc).__name__}，请检查答卷、定义版本及 PDF 依赖。", file=sys.stderr)
    print(f"完成：找到 {len(sources)} 份，生成 {generated} 份，跳过 {skipped} 份，失败 {failed} 份。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
