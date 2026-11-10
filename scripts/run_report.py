"""Generate and review a local report through the configured DashScope LLM."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.report_pipeline import PipelineError, load_prompts, prepare_case, run_report


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 llm/ 和根目录 .env 的 DashScope 配置生成、审核报告。")
    parser.add_argument("model_input", type=Path, help="答卷版本目录中的 model_input.json")
    parser.add_argument("--definition", type=Path, default=PROJECT_ROOT / "questionnaire/definition.json")
    parser.add_argument("--prompt-dir", type=Path, default=PROJECT_ROOT / "prompt")
    parser.add_argument("--check", action="store_true", help="仅检查输入、授权、转译一致性及提示词，不调用模型或创建报告文件")
    args = parser.parse_args()
    try:
        if args.check:
            case = prepare_case(args.model_input, args.definition)
            load_prompts(args.prompt_dir)
            print(f"检查通过：{len(case.allowed_refs)} 条可用转译记录；未调用模型。")
            return 0
        result = run_report(args.model_input, definition_path=args.definition, prompt_dir=args.prompt_dir)
    except PipelineError as exc:
        print(f"{exc.code}：{exc.message}", file=sys.stderr)
        return 2
    print(f"运行目录：{result['run_dir']}")
    if result["status"] == "approved":
        print(f"审核结果：{result['decision']}；最终报告：{Path(result['run_dir']) / 'final_report.json'}")
        return 0
    if result["status"] == "hold":
        print("审核暂缓：请查看 review_report.json，本次未发布最终报告。")
        return 3
    print(f"{result['stage']} 阶段失败：{result['error']['message']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
