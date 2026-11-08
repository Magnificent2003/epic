# 早孕期非计划妊娠女性妊娠选择决策辅助智能体

The EPIC System: An AI-Powered Decision Support Agent for Women Experiencing Unintended Pregnancy in Early Pregnancy

填写者通过问卷整理本人意愿、健康情况、现实条件与支持需求。系统保留回答中的未知与矛盾，不将经济压力或他人意见换算为去留评分。

**一期：问卷填写、答案核对与本地保存。** 后续接入已有 `llm/` 模块，生成个人支持报告，提供预览与 PDF 下载。当前采用单个 Python 服务和本地 JSON 文件，无需数据库、外部存储桶或账号系统。

## 项目结构

```text
.
├── app/                         # 问卷页面与交互
├── backend/                     # 页面服务、答卷校验与存储
├── questionnaire/definition.json # 题目、选项、字段及跳题规则
├── 意外妊娠决策支持问卷.md        # 问卷阅读版
├── llm/                         # 后续报告使用的模型调用模块
├── scripts/run_backend.py       # 启动入口
├── tests/                       # 问卷规则与接口测试
├── requirements.txt             # 模型调用依赖
└── work_dir/<submission_id>/     # 本地答卷，不提交到版本库
    ├── v1/answers.json
    └── v2/answers.json
```

## 启动

使用 Python 3.10 或更高版本。问卷前后端无需安装额外依赖，也无需配置模型密钥。

```bash
python3 scripts/run_backend.py
```

打开 **http://127.0.0.1:8000**。可用 `--port 8001` 更换端口，或用 `--storage-root /tmp/epic` 指定答卷目录。

## 填写与保存

1. 确认自愿填写与私密环境，按主题逐步作答；业务题可跳过或明确拒答。
2. 页面根据回答处理跳题、互斥与限选，最后核对答案并设置报告使用范围。
3. 确认后保存为本地 JSON；再次修改并确认会创建新版本，保留原版本。

未提交的回答只保留在当前页面；如需稍后继续，可下载草稿并在下次导入。

题目与规则以 `questionnaire/definition.json` 为准。回答区分已答、未答、拒答、不适用和规则跳过；“无”“不清楚”保留为明确选项。当前不调用模型、不生成报告；临床分流规则在报告阶段另行确定。

## 测试

前端规则测试需要 Node.js 18 或更高版本。

```bash
python3 -m unittest discover -s tests -v
node tests/frontend.test.mjs
```

后续接入模型时，安装 `requirements.txt` 并按 [llm/README.md](llm/README.md) 配置 `.env`。
