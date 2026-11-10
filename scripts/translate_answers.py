"""Backfill a local answer revision without invoking a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.translation import OUTPUT_FILENAME, translate_file
from backend.validation import ValidationError


def main() -> int:
    parser = argparse.ArgumentParser(description="将 answers.json 转译为同目录的 model_input.json，保留原始答卷。")
    parser.add_argument("answers", type=Path, help="需要转译的 answers.json 路径")
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire" / "definition.json")
    args = parser.parse_args()
    try:
        definition = json.loads(args.definition.read_text(encoding="utf-8"))
        result = translate_file(args.answers, definition)
    except ValidationError as exc:
        print("未生成可用的模型输入：", file=sys.stderr)
        for issue in exc.errors:
            print(f"  {issue['field']}：{issue['message']}", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("转译失败：请检查输入路径、问卷定义和文件读写权限。", file=sys.stderr)
        return 1
    print(args.answers.parent / OUTPUT_FILENAME)
    if result["status"] != "ready":
        for issue in result["issues"]:
            print(f"暂不可用于推理：{issue['message']}", file=sys.stderr)
        return 2
    print(f"已转译 {len(result['lines'])} 项；原始答卷保持不变，未调用模型。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
