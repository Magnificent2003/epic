# 早孕期非计划妊娠女性妊娠选择决策辅助智能体

The EPIC System: An AI-Powered Decision Support Agent for Women Experiencing Unintended Pregnancy in Early Pregnancy

填写问卷后，系统自动完成答卷转译、报告生成、模型审核和 HTML/PDF 排版。报告整理本人意愿、健康情况、现实条件与支持需求，保留未知与矛盾，不计算去留评分。

采用单个 Python 服务和本地文件，无需数据库、外部存储桶或账号系统。

## 项目结构

```text
.
├── app/                         # 问卷交互、等待页面与报告样式
├── backend/                     # 校验、存储、后台任务与报告渲染
├── questionnaire/definition.json # 题目、跳题与转译规则
├── doc/                         # 问卷阅读版及原始报告模板
├── prompt/                      # 生成、审核提示词及输出格式
├── llm/                         # 统一模型调用模块
├── scripts/                     # 服务启动和各阶段命令
├── tests/                       # 规则、接口和浏览器测试
└── work_dir/<submission_id>/v<n>/
    ├── answers.json             # 完整原始回答与状态
    ├── questionnaire.pdf        # 原始问卷、完整选项及作答记录（开发者对照）
    ├── model_input.json         # 获准使用的自然语言记录
    ├── final_report.json        # 审核后的最终报告
    ├── report.html / report.pdf # 阅读页面与下载文件
    └── reports/<run_id>/        # 每次模型运行的草稿、审核及状态
```

## 启动

使用 Python 3.10 或更高版本。在根目录 `.env` 配置 `LLM_PROVIDER=DashScope`、`DASHSCOPE_API_KEY` 和 `DASHSCOPE_MODEL`；可参考 `.env.example`。完整报告和审核修订需要足够的输出额度，当前采用 `LLM_MAX_TOKENS=8192`。更多配置见 [llm/README.md](llm/README.md)。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/run_backend.py
```

默认监听 `0.0.0.0:8000`，启动时打印本机和局域网访问网址（含实际端口）。同一局域网的设备打开日志中的“局域网访问”网址即可。启动命令支持 `--host`、`--port` 和 `--storage-root`。

WSL 镜像网络下，若其他设备无法访问，在 Windows **管理员 PowerShell** 中执行一次 `powershell -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\magnificent\code\epic\scripts\wsl_lan.ps1"`（路径按实际项目位置调整）。脚本只放行当前局域网到 TCP 8000 的 Windows 防火墙规则，WSL 镜像网络默认继承该规则，并打印网址；自定义端口加 `-Port 端口号`，撤销加 `-Remove`，更换网络后重跑。然后按上方命令在 WSL 启动服务。

## 填写与报告

1. 自愿填写并核对回答，明确报告使用范围；未提交时可保存或导入草稿。
2. 提交后保存答卷并在后台生成、审核，页面显示实际进度；刷新或收藏当前地址可恢复查询。
3. 完成后查看报告或下载 PDF。返回修改并再次提交会创建新版本。

普通未答、拒答和条件跳过默认不进入模型；关键缺失仅提示缺少依据。多选只陈述勾选项，限选帮助题不推导排名；排除字段及其依赖不进入正文。授权或文字排除要求未明确时阻止生成。

审核 `pass` 沿用草稿，`revise` 采用校验后的修订稿，`hold` 暂缓展示。运行失败或服务中断后由本人点击重试，不自动重复调用模型。仅已通过审核、与当前答卷一致的报告可查看和下载；页面与 PDF 都由脚本排版，不再调用模型或改写正文。

后台任务使用本机进程内队列，`report_status.json` 记录进度；`run.json` 的 `approved` 表示模型链路通过，`report_status.json` 的 `ready` 表示页面与 PDF 已可用。PDF 自动分页，优先嵌入本机中文字体；可用 `EPIC_PDF_FONT` 指定字体文件。

## 单独运行某一步

```bash
.venv/bin/python scripts/translate_answers.py work_dir/<id>/v1/answers.json
.venv/bin/python scripts/run_report.py work_dir/<id>/v1/model_input.json
.venv/bin/python scripts/render_report.py work_dir/<id>/v1/final_report.json
.venv/bin/python scripts/export_questionnaires.py
```

命令行生成的报告保存在 `reports/<run_id>/`，渲染时传入该次 `final_report.json` 的实际路径；HTML/PDF 保存于最终稿同目录。`run_report.py --check` 只检查输入，不调用模型。渲染需同目录审核记录及对应原始答卷，不能仅凭任意 JSON 导出。

`export_questionnaires.py` 无需参数，递归遍历项目 `work_dir`（含测试子目录），在每份 `answers.json` 旁生成黑白 `questionnaire.pdf`，已有文件跳过（`--force` 可重建）。保留提交时间（北京时间）、原题说明、全部选项、勾选/填写与未答/拒答/跳题状态，包括报告排除项；此步骤不调用模型。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
node tests/frontend.test.mjs
```

前端规则测试需要 Node.js 18+。可选浏览器验收脚本为 `tests/browser_smoke.py` 和 `tests/report_browser_smoke.py`，需要 Playwright/Chromium。自动测试使用模拟模型，不发送真实请求。

## 联系作者

- **作者**：李峙
- **单位**：浙江大学
- **GitHub**：[Magnificent2003](https://github.com/Magnificent2003)
- **邮箱**：1375173994@qq.com
