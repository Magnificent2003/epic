# LLM-Invoker

一个轻量的 Python 模型调用工具，主体代码位于 `llm/`。用统一的同步接口调用文本模型和视觉语言模型，返回完整答案，方便接入自己的业务代码。

- `LLMInvoker`：文本输入，返回字符串。
- `VLMInvoker`：文本、单图或多图输入，系统提示词与用户提示词可分别设置、消融。
- `chat_json()`：调用模型并解析 JSON，支持重试和指数退避。
- 支持 DashScope/Qwen、OpenAI 及使用相同 Chat Completions 协议的第三方接口。
- 可用根目录 `.env` 配置，也可显式传入 `LLMSettings` / `VLMSettings`。

每次调用独立，不自动保留对话历史。库内部不打印答案；`chat()` 始终返回完整的 `str`，由调用方决定何时打印或处理。

## 安装

需要 Python 3.10 或更高版本。目前未提供安装包配置，建议从仓库根目录运行，或将 `llm/` 放入自己的项目。

仅使用核心调用功能：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install "openai>=1.0.0" "python-dotenv>=1.0.0" httpx
cp .env.example .env
```

也可以运行 `python -m pip install -r requirements.txt`。该文件包含模型调用所需依赖；问卷前后端无需安装。

## 快速开始

在根目录 `.env` 填写服务商配置，例如 DashScope：

```dotenv
LLM_PROVIDER=dashscope
VLM_PROVIDER=dashscope
DASHSCOPE_API_KEY=your_dashscope_key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus
DASHSCOPE_VLM_MODEL=qwen-vl-plus
```

文本调用：

```python
from llm import LLMInvoker

ai = LLMInvoker()
text = ai.chat(
    prompt="用三句话介绍 Python。",
    system="你是一名编程教师，请使用中文回答。",
)
print(text)
```

多图调用：

```python
from llm import VLMInvoker

ai = VLMInvoker()
text = ai.chat(
    system_prompt="你是一名图像分析助手。",
    user_prompt="请逐张分析这些图片，比较各张图片的异同。",
    image_paths=["./image_1.png", "./image_2.png"],
)
print(text)  # 收到完整答案后，一次性打印
```

实际模型需要支持所用的图片格式、多图数量和消息角色。上述模型名是配置示例，使用时按服务商提供的模型填写。

## VLM 输入与提示词消融

推荐使用 `system_prompt`、`user_prompt`、`image_paths` 三个参数。

| 输入 | 行为 |
|---|---|
| 不传 `system_prompt` 或设为 `None` | 使用 `llm/prompt.py` 中的默认 `SYSTEM_PROMPT` |
| `system_prompt=""` 或仅含空白字符 | 不发送 system 消息，适合消融系统提示词 |
| `user_prompt=""` 或仅含空白字符 | 不发送用户指令文字，仍发送图片 |
| `image_paths=[]`、`None` 或不传 | 允许纯文本调用，用户提示词必须有内容 |
| 用户提示词与图片同时为空 | 抛出 `ValueError`，即使提供了系统提示词 |

分别消融提示词时，可以复用同一个实例和图片列表：

```python
images = ["./image_1.png", "./image_2.png"]
system_prompt = "你是一名图像分析助手，请仔细观察图像中的细节。"
user_prompt = "请逐张分析这些图片，比较各张图片的异同。"

# 完整提示词
text = ai.chat(
    system_prompt=system_prompt,
    user_prompt=user_prompt,
    image_paths=images,
)

# 消融系统提示词
without_system = ai.chat(
    system_prompt="",
    user_prompt=user_prompt,
    image_paths=images,
)

# 消融用户提示词
without_user = ai.chat(
    system_prompt=system_prompt,
    user_prompt="",
    image_paths=images,
)

# 两段提示词均消融，仅发送图片
images_only = ai.chat(system_prompt="", user_prompt="", image_paths=images)

# 无图对照
text_only = ai.chat(system_prompt=system_prompt, user_prompt=user_prompt, image_paths=[])
```

`image_paths` 可传单个 `str` / `pathlib.Path`，也可传它们组成的序列，按输入顺序发送：

- 本地路径：相对路径按当前工作目录解析，自动编码为 Base64 data URL；支持 `~`。
- `http://` / `https://` URL：直接交给模型服务读取。
- `data:image/...;base64,...`：直接发送已有的 data URL。

```python
from pathlib import Path

text = ai.chat(
    system_prompt="",
    user_prompt="描述这张图片。",
    image_paths=Path("./image_1.png"),
)
```

默认不添加额外的图片标签。`chat()` 和 `chat_json()` 都可设置 `label_images=True`，在每张图片前添加 `第 N 张图片：`。做提示词消融时保持该选项一致；启用后，即使 `user_prompt=""`，图片标签也仍会发送。

### 旧接口兼容

原有 `prompt`、`images`、`system` 关键字和位置参数继续支持：

```python
text = ai.chat(prompt="比较图片。", images=["./image_1.png", "./image_2.png"], system="")
text = ai.chat("比较图片。", ["./image_1.png", "./image_2.png"], "")
```

对应关系是 `prompt` → `user_prompt`、`images` → `image_paths`、`system` → `system_prompt`。同一含义的新旧参数不能同时传入，例如同时传 `prompt` 和 `user_prompt` 会抛出 `TypeError`，即使二者内容相同。

新参数命名适用于 `VLMInvoker`；`LLMInvoker` 继续使用 `chat(prompt, system=..., extra_body=...)`。

## 完整返回与 SSE 接收

默认使用普通非流式请求，接口返回完整 JSON 后提取答案。

部分 OpenAI 兼容服务需要以 SSE 流式方式接收。设置 `VLM_REQUEST_STREAM=1` 后，Provider 在内部收集正文片段、跳过无正文的统计片段，接收结束后返回完整字符串。调用方仍使用同样的 `chat()` 和一次 `print(text)`，不会拿到迭代器。

这是显式配置：普通请求失败后，不会自动重发为流式请求。接收过程中出错会抛出 `ProviderError`，不会把部分正文作为完整答案返回。`VLM_REQUEST_STREAM` 只影响 VLM 调用。

之前多图 demo 使用的第三方接口，可在根目录 `.env` 配置为：

```dotenv
VLM_PROVIDER=openai
OPENAI_API_KEY=your_third_party_key
OPENAI_BASE_URL=https://aiapi.world/v1
OPENAI_VLM_MODEL=gpt-6-astra
VLM_REQUEST_STREAM=1
VLM_TIMEOUT_S=120
VLM_TEMPERATURE=none
VLM_MAX_TOKENS=none
VLM_TRUST_ENV=0
```

这里 `none` 表示请求中不发送对应生成参数，沿用原 demo 的行为。更换接口或模型时，按服务方的要求配置是否使用 SSE、生成参数和超时。`OPENAI_API_KEY` 与 `OPENAI_BASE_URL` 同时供 `openai` Provider 的 LLM 和 VLM 使用，模型名分别配置。

## JSON 输出

文本模型：

```python
from llm import LLMInvoker

ai = LLMInvoker()
data = ai.chat_json(
    prompt='请介绍 Python，返回包含 "name" 和 "description" 的 JSON 对象。',
    system="只输出一个 JSON 对象，不要附加解释或 Markdown。",
    retries=2,
    backoff_s=0.8,
)
print(data)
```

视觉模型的 `chat_json()` 支持与 `chat()` 相同的新旧输入参数：

```python
from llm import VLMInvoker

ai = VLMInvoker()
data = ai.chat_json(
    system_prompt="只输出一个 JSON 对象，包含 summary 和 differences 字段。",
    user_prompt="比较两张图片。",
    image_paths=["./image_1.png", "./image_2.png"],
    retries=2,
    backoff_s=0.8,
)
```

先尝试直接 `json.loads()`；失败后尝试截取第一个 `{` 到最后一个 `}` 再解析。调用或解析失败时，按 `backoff_s * 2**重试序号` 等待后重试；`retries=2` 表示最多请求三次，最终失败抛出 `ProviderError`。该方法不做 JSON Schema 校验，需要严格结构时由业务层验证。VLM 的新旧参数冲突、提示词及图片输入错误会在重试前报错，不会发送请求。

## 配置

### 配置加载顺序

不传 settings 时，Invoker 固定读取仓库根目录 `.env`，使用 `override=True`，因此文件中的值会覆盖同名进程环境变量；文件中未出现的变量仍从进程环境读取。根目录没有 `.env` 时，可直接使用进程环境变量。

`llm/.env` 是已废弃的位置；检测到该文件会直接报错。请只保留根目录配置。

显式传入 `LLMSettings` / `VLMSettings` 时，跳过 `.env` 加载与上述旧文件检查：

```python
import os
from llm import VLMInvoker, VLMSettings

settings = VLMSettings(
    provider="openai",
    api_key=os.environ["MY_VLM_API_KEY"],
    base_url="https://aiapi.world/v1",
    model="gpt-6-astra",
    timeout_s=120,
    temperature=None,
    max_tokens=None,
    request_stream=True,
    trust_env=False,
)
ai = VLMInvoker(settings=settings)
```

`LLMSettings` 同样接受 `provider`、`api_key`、`base_url`、`model`、`timeout_s`、`temperature`、`max_tokens`、`trust_env`，用法为 `LLMInvoker(settings=...)`；`request_stream` 是 `VLMSettings` 的独立配置。

### 通用环境变量

| 变量 | 含义 | 默认值 / 回退 |
|---|---|---|
| `LLM_PROVIDER` | 文本 Provider：`dashscope` / `openai` | `dashscope` |
| `VLM_PROVIDER` | 视觉 Provider：`dashscope` / `openai` | 回退到 `LLM_PROVIDER`，最终为 `dashscope` |
| `LLM_TIMEOUT_S` | 文本请求超时，单位秒 | `60.0` |
| `LLM_TEMPERATURE` | 文本采样温度 | `0.2` |
| `LLM_MAX_TOKENS` | 文本最大输出 token 数 | `512` |
| `LLM_TRUST_ENV` | 使用系统代理等环境配置 | `0` |
| `VLM_TIMEOUT_S` | 视觉请求超时，单位秒 | 回退到 `LLM_TIMEOUT_S` |
| `VLM_TEMPERATURE` | 视觉采样温度 | 回退到 `LLM_TEMPERATURE` |
| `VLM_MAX_TOKENS` | 视觉最大输出 token 数 | 回退到 `LLM_MAX_TOKENS` |
| `VLM_TRUST_ENV` | 视觉调用使用系统代理等环境配置 | 回退到 `LLM_TRUST_ENV` |
| `VLM_REQUEST_STREAM` | 视觉请求使用 SSE，内部聚合后完整返回 | `0`，不回退到 LLM 配置 |

布尔值可使用 `1/0`、`true/false`、`yes/no`、`on/off`，不区分大小写。

`LLM_TEMPERATURE`、`LLM_MAX_TOKENS`、`VLM_TEMPERATURE`、`VLM_MAX_TOKENS` 可设为 `none` 或 `null`，不区分大小写，表示不发送对应请求参数；显式 settings 中使用 Python 的 `None`。环境变量留空表示采用默认值或回退值，**不表示省略参数**。

默认不读取系统代理。如果需要使用 `HTTP_PROXY` / `HTTPS_PROXY` 等代理环境变量，设置相应的 `LLM_TRUST_ENV=1` 或 `VLM_TRUST_ENV=1`。

### Provider 环境变量

| 变量 | 默认值 / 要求 |
|---|---|
| `DASHSCOPE_API_KEY` | 使用 `dashscope` 时必填 |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `DASHSCOPE_MODEL` | `qwen-plus` |
| `DASHSCOPE_VLM_MODEL` | `qwen-vl-plus` |
| `OPENAI_API_KEY` | 使用 `openai` 时必填，可填所配置第三方服务的密钥 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `OPENAI_VLM_MODEL` | `gpt-4o-mini` |

VLM 的模型名使用各自的 `*_VLM_MODEL`，不会回退到 `*_MODEL`。`spark` / `SPARK_*` 仅为预留接口，当前未实现调用。

### 额外请求参数

`chat()` 和 `chat_json()` 都接受 `extra_body`，用于传入服务商扩展字段，例如：

```python
text = ai.chat(
    system_prompt="",
    user_prompt="描述图片中的主要内容。",
    image_paths=["./image_1.png"],
    extra_body={"enable_thinking": False},
)
```

扩展字段是否可用取决于模型和接口。`extra_body` 不允许传 `stream`，应通过 `VLM_REQUEST_STREAM` / `VLMSettings.request_stream` 设置，以保持接收方式与 SDK 一致；`stream_options` 可按服务端要求透传。

## 异常与测试

异常从 `llm.errors` 导入：

```python
from llm.errors import MissingAPIKeyError, ProviderError
```

- `MissingAPIKeyError`：缺少所选 Provider 的密钥。
- `ProviderError`：底层请求、响应处理或 JSON 重试失败。
- `TypeError` / `ValueError`：输入参数冲突或输入不合法。
- `FileNotFoundError`：指定的本地图片不存在。

运行项目测试：

```bash
python -m unittest discover -s tests -v
```

测试使用模拟响应，不发送真实 API 请求。本页中的调用示例会请求实际配置的模型服务。

## 项目结构与扩展

```text
llm/
├── __init__.py             # 对外导出 Invoker 与 settings
├── config.py               # LLM / VLM 配置及环境变量解析
├── errors.py               # 异常
├── invoker.py              # 文本调用
├── vlm_invoker.py          # 视觉调用、提示词及图片消息构建
├── prompt.py               # 默认系统提示词
└── providers/              # Provider 协议和各服务商实现
.env.example                # 配置模板
```

新增 Provider 时，实现 `llm/providers/base.py` 中的 `ChatProvider` 协议，在 `providers/__init__.py` 导出，并补充 `config.py` 及对应 Invoker 的初始化分支。Provider 应返回完整字符串；如果服务要求流式传输，在 Provider 内部完成收集。

当前使用同步 Chat Completions 接口，不提供异步调用、对话历史管理或对外流式生成器。
