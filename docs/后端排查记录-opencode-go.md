# 接入 opencode go（deepseek-v4-pro）排查记录

> 记录把本项目从「连不上后端」修到「完整跑通」的全过程。
> 这次一共叠了 **4 个独立的 bug**，每修掉一个就暴露下一个，逐层往下。
> 最终验证：主持人 → 维度陈述 → 策展人高维还原，整条流水线在
> `opencode go + deepseek-v4-pro` 上无报错跑完。

---

## TL;DR（最终正确配置）

`.env`：

```ini
LLM_BASE_URL=https://opencode.ai/zen/go/v1   # 到 /v1 为止，不带 /chat/completions
LLM_API_KEY=<你的 opencode key>
LLM_MODEL=deepseek-v4-pro                     # 不带 opencode-go/ 前缀
LLM_TEMPERATURE=0.75
```

代码侧三处关键点（见下文「修复清单」）：

1. `config.py` 用 `OpenAIChatCompletionClient`，**不要**用 `OpenAIChatClient`。
2. `roundtable.py` 不用 `instructions=`，把人设折叠进 user 消息。
3. `roundtable.py` 的 `_ask` 必须用流式 `run(..., stream=True)`。

---

## 四层 bug，逐层剖析

### Bug 1 · base_url 多写了 `/chat/completions`

**现象**

```
ChatClientException: OpenAIChatClient service failed to complete the prompt
```

**错误配置**

```ini
LLM_BASE_URL=https://opencode.ai/zen/go/v1/chat/completions
```

**原理**

OpenAI 兼容客户端（`OpenAIChatClient` / `OpenAIChatCompletionClient` 底层的
`openai` SDK）会自己在 `base_url` 后面拼接接口路径（`/chat/completions`
或 `/responses`）。所以 `base_url` 只能填到 **`/v1`** 为止。

官网文档表格里写的 `https://opencode.ai/zen/go/v1/chat/completions` 是
**完整请求地址**，不是 base_url。直接当 base_url 用，实际请求会变成：

```
https://opencode.ai/zen/go/v1/chat/completions/chat/completions   → 404
```

**关于模型名**：官网说「OpenCode 配置中用 `opencode-go/<model-id>` 格式」——
那是 OpenCode 那个 CLI 工具自己的配置文件写法。我们这里是直接走
OpenAI 兼容 API，`model` 字段填裸 ID `deepseek-v4-pro` 即可。实测
`opencode-go/deepseek-v4-pro` 会报 `Model ... is not supported`。

**修复**

```ini
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=deepseek-v4-pro
```

**经验**：所有 OpenAI 兼容后端（Ollama / DeepSeek / OpenAI 官方 / 各类中转站）
的 base_url 都以 `/v1` 结尾，看 `.env.example` 的示例就是这个规律。

---

### Bug 2 · 后端不接受 system 消息

**现象**

```
Error code: 400 - {"detail":"System messages are not allowed"}
```

换不同的中转站后，又遇到 opencode go 上的同类表现：带 system 的请求返回
`{"type":"error","error":{"type":"error","message":"Internal server error"}}`。

**定位手段（curl 二分法）**

直接用 curl 单独打端点，把「带 system」和「不带 system」对比：

```bash
# 带 system —— 报错
curl https://opencode.ai/zen/go/v1/chat/completions \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-pro","messages":[{"role":"system","content":"你是助手"},{"role":"user","content":"hi"}]}'
# → Internal server error

# 不带 system —— 正常返回
curl ... -d '{"model":"deepseek-v4-pro","messages":[{"role":"user","content":"hi"}]}'
# → 正常 JSON
```

**原理**

本项目给主持人、策展人、10 个维度 agent 都通过
`client.as_agent(instructions=...)` 注入人设。`agent_framework` 会把
`instructions` 作为 **`system` 角色消息**发给后端。而 opencode go 这个通道
**不接受 system 角色**，直接 400 / 500。

> 这类限制在「推理模型」通道上很常见（早期 o1 系列也不允许 system），
> 不是本项目代码的 bug，而是后端协议差异。

**修复思路：把人设折叠进 user 消息，全程不发 system。**

`roundtable.py` 的 `__post_init__`：建 agent 时**去掉** `instructions=`，
改成把人设文本存进一个 `dict`，由 `_ask` 在每次提问时拼到 user 提示最前面：

```python
# 建 agent：不传 instructions（否则会变成 system 消息）
self._moderator   = client.as_agent(name=MODERATOR_NAME,   default_options=opts)
self._synthesizer = client.as_agent(name=SYNTHESIZER_NAME, default_options=opts)
self._dim_agents  = {d.key: client.as_agent(name=d.name, default_options=opts)
                     for d in self.dimensions}

# 人设单独存表：agent.name -> instructions
self._persona = {
    MODERATOR_NAME:   MODERATOR_INSTRUCTIONS,
    SYNTHESIZER_NAME: SYNTHESIZER_INSTRUCTIONS,
    **{d.name: d.instructions for d in self.dimensions},
}
```

`_ask` 里拼接：

```python
persona = self._persona.get(agent.name, "")
full = f"【你的角色设定】\n{persona.strip()}\n\n——以下是本轮任务——\n\n{prompt}" \
       if persona else prompt
```

> 注意：`description=d.focus` 是 agent 的元数据，不会发给模型，保不保留都行；
> 真正会变 system 的只有 `instructions`。

---

### Bug 3 · 客户端默认走 `/responses` 端点（404）

**现象**

```
openai.NotFoundError: <!DOCTYPE html>... 404 - Page Not Found | opencode
```

报错栈里能看到调用的是 `client.responses.with_raw_response.create`——
打的是 `/responses`，返回了 opencode 官网的 404 HTML 页面。

**原理**

`agent_framework.openai` 同时提供两个客户端：

| 类 | 实际调用的端点 | 适用场景 |
|---|---|---|
| `OpenAIChatClient` | `/responses`（OpenAI 新版 Responses API） | 仅 OpenAI 官方及少数支持 Responses 的服务 |
| `OpenAIChatCompletionClient` | `/chat/completions`（经典接口） | **绝大多数兼容网关**（opencode go、中转站、Ollama…） |

opencode go 只实现了 `/chat/completions`，没有 `/responses`，所以
`OpenAIChatClient` 必然 404。

**修复**：`config.py` 换类。

```python
# 之前（错）：from agent_framework.openai import OpenAIChatClient
from agent_framework.openai import OpenAIChatCompletionClient

def build_client(settings):
    return OpenAIChatCompletionClient(
        model=settings.model, api_key=settings.api_key, base_url=settings.base_url,
    )
```

**经验**：接 OpenAI 兼容网关时，默认就用 `OpenAIChatCompletionClient`。
只有确认对方支持 Responses API（基本只有 OpenAI 官方）才用 `OpenAIChatClient`。

---

### Bug 4 · 推理模型 + 本地代理 → 非流式连接被掐断

**现象**

```
httpx.RemoteProtocolError: Server disconnected without sending a response.
→ openai.APIConnectionError: Connection error.
```

诡异点：用 `curl` 和「最小 `hi` 请求」都能成功，但项目里那条更长的主持人
提示一发就断。

**定位手段（非流式 vs 流式对比）**

用同一条长提示，分别测非流式和流式：

```
NONSTREAM FAIL: APIConnectionError Connection error.
STREAM OK in 48.7s, chunks=1601
```

**原理（关键）**

1. `deepseek-v4-pro` 是**推理模型**，在吐出第一个字节前要先「思考」几十秒
   （这次实测 ~48s 才完成）。
2. 非流式请求期间，这几十秒里 TCP 连接上**没有任何数据流动**。
3. 本机走了系统代理 `HTTP(S)_PROXY=http://127.0.0.1:10809`（v2ray/clash 那类）。
   这类代理对「迟迟收不到响应头的空闲连接」有超时，会主动 `Server disconnected`。
4. 短请求（`hi`）思考时间短，赶在代理超时前返回，所以「碰巧能成」——具有欺骗性。
5. **流式**（`stream=True`）下，token 持续往回流，连接一直有数据，不会被判定空闲，
   于是能撑过几十秒的完整生成。

> 一句话：**推理模型 + 中间有代理/网关 → 必须流式**，否则首字节前的静默期
> 会被中间层当成死连接掐掉。这跟 base_url、模型名都无关，是传输层问题。

**修复**：`roundtable.py` 的 `_ask` 改用流式并累加。

```python
parts: list[str] = []
async for upd in agent.run(full, stream=True):
    if getattr(upd, "text", None):
        parts.append(upd.text)
return "".join(parts).strip()
```

> `agent_framework` 的 `Agent` 没有 `run_stream`，而是 `run(..., stream=True)`，
> 返回一个可 `async for` 迭代的 `ResponseStream`，每个 chunk 的增量文本在 `.text`。

**其他可缓解的方向（本项目没采用，备查）**

- 给后端直连、绕开本地代理：`AsyncOpenAI(..., http_client=httpx.AsyncClient(trust_env=False))`，
  或清掉 `HTTP_PROXY/HTTPS_PROXY`。但若后端本身需要代理才能访问，就只能靠流式。
- 调大客户端超时：对「代理主动断连」这种 `RemoteProtocolError` **无效**
  （它不是超时异常，是对端把连接关了）。

---

## 修复清单（按文件）

**`.env`**
```ini
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=deepseek-v4-pro
```

**`config.py`**
- `OpenAIChatClient` → `OpenAIChatCompletionClient`（走 `/chat/completions`）。

**`roundtable.py`**
- `__post_init__`：建 agent 去掉 `instructions=`，人设存进 `self._persona`。
- `_ask`：① 把人设折叠进 user 消息；② 用 `run(..., stream=True)` 流式累加。

---

## 排查方法论（可复用）

1. **先用 curl 把后端单独打通**，再谈代码。能隔离掉「框架/代码」这一层。
2. **二分对比**缩小变量：带/不带 system、流式/非流式、最小提示/真实提示、
   走代理/直连——每次只改一个变量，看现象翻转点。
3. **认真读报错栈最底层那一行**：
   - `client.responses.create` → 端点用错了（Bug 3）。
   - `RemoteProtocolError: Server disconnected` → 传输层/代理，不是 API 语义（Bug 4）。
   - `{"detail": "..."}` 这种结构化 body → 是后端业务拒绝（Bug 2）。
4. **「碰巧能成」要警惕**：最小请求成功 ≠ 真实负载成功，差异往往在
   payload 大小或响应时长（Bug 4 就是被短请求误导）。

## 换后端时的快速 checklist

- [ ] base_url 到 `/v1` 为止，不带接口路径。
- [ ] model 用裸 ID，别带工具专用前缀。
- [ ] 优先 `OpenAIChatCompletionClient`；除非确认对方支持 Responses。
- [ ] 后端是否接受 system？不接受就走「人设折叠进 user」。
- [ ] 是不是推理模型 / 有没有走代理？是则保持流式。
- [ ] 跑通后**轮换在调试中暴露过的 API key**。
