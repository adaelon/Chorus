# group_relay — Chorus ↔ AstrBot 窄消息桥

只搬字节，不含业务智能（LLM/人设/调度/记忆全在编排服务）。已实现 **S4.1 出站 + S4.2 入站 + S4.3 多 bot 映射**。

## 多 bot 配置（S4.3，手动）

1. **BotFather**：为每个 AI 身份建一个 telegram bot，拿各自 token；对每个 bot 发
   `/setprivacy` → **Disable**（关 privacy mode，否则 bot 收不到群里其他人的消息）。
2. **AstrBot**：在 WebUI/`data/cmd_config.json` 的 `platform` 列表里加 N 个 telegram 实例
   （各填 token），每个实例有唯一 **id**（即 `bot_id`）。N 个实例都应 RUNNING。
3. **映射**：在好友页给每个 Contact 填 `bot_ref` = 对应 telegram 实例 id。大脑出站时
   据 `contact.bot_ref` 把"该 contact 发言"路由到对应 bot（`OutboundClient`，orchestrator 侧）。

## 位置与运行

本插件**就地**位于 `Chorus/astrbot/data/plugins/group_relay/`——AstrBot 框架以 vendored
形式放在 `Chorus/astrbot/`（框架本身 .gitignore 不提交，仅本插件目录进 git）。改即生效、
无需复制/软链。AstrBot 从 `Chorus/astrbot` 启动（conda env `astrbot_env`），data 根即
`Chorus/astrbot/data`，本插件被识别为 `data.plugins.group_relay`。

重启 / 重载插件后，`initialize()` 在 `127.0.0.1:9876` 起出站桥（端口可在插件配置
`bridge_port` 改）。需先在 AstrBot 配好至少一个 platform 实例（如某 telegram bot），其
实例 id 即出站用的 `bot_id`（= `Contact.bot_ref`）。

## 出站 API

```
POST http://127.0.0.1:9876/outbound
{ "group_key": "<unified_msg_origin>", "bot_id": "<platform 实例 id>", "text": "..." }
```

- `group_key` = AstrBot `unified_msg_origin`，形如 `platform:type:session`；平台段发送时被换成 `bot_id`。
- 成功 → `{"ok": true, "bot_id": ..., "session": "bot_id:type:session"}`，群里以该 bot 身份发出 `text`。
- 未找到该 bot 实例 → 404；缺字段/非法 group_key → 400。

## 入站 API（S4.2）

群消息钩子（`@filter.event_message_type(GROUP_MESSAGE)`，高优先级）规范化每条群消息成
InboundMsg → POST `{brain_url}/relay/inbound`（`brain_url` 可在插件配置改，默认 :8900）：

```
{ group_key, platform, sender_id, sender_name, sender_kind, text, native_msg_id, ts }
```

- **去重**：按**内容键**（去平台段 session + sender_id + ts + text）先到先得——多 bot 在同群各收到同一条人类消息，只转一次。（实测 telegram 给各 bot 的 `message_id` 不一致，故不能按 msg_id 去重。）
- **截断**：转发后（及重复副本）`event.stop_event()`，阻止 AstrBot 用自己的 provider 自动回复。
- 自己（本 bot）发的 / 空文本 → 忽略（不转发不截断）；多 bot 间 AI 发言识别留 S4.3。
- 大脑侧对 InboundMsg 的处理（入会话/路由）= S4.4；当前默认 POST 到 `/inbound`，契约对接随 S4.4 定。

## 判据（手动，单 bot）

**出站**：
1. AstrBot 配一个 telegram bot 实例（记其实例 id）、把 bot 拉进一个群、发一条消息拿到该群的 `unified_msg_origin`（日志/调试可见）。
2. `curl -X POST http://127.0.0.1:9876/outbound -H 'Content-Type: application/json' -d '{"group_key":"<umo>","bot_id":"<实例id>","text":"出站测试"}'`
3. 期望：该 bot 在目标群发出"出站测试"，curl 返回 `{"ok":true,...}`。

**入站**：在群里发一条消息 → 大脑 `/inbound` 收到一条 InboundMsg；**AstrBot 自身不自动回复**（stop_event 生效）。

## 测试

- **离线纯逻辑**（去重/决策/规范化，无需 astrbot）：
  `orchestrator/.venv/Scripts/python -m pytest astrbot/data/plugins/group_relay/tests`
- **入站 smoke**（真实 astrbot 加载插件 + 假大脑收 POST，验转发/去重/stop_event，无需 telegram）：
  ```
  cd E:\allwork\download\agent\Chorus\astrbot
  E:\AnacondaEnvs\astrbot_env\python.exe data\plugins\group_relay\smoke_inbound.py
  ```
- **出站精确路由 smoke**（真实 astrbot + 两个假 platform，验 bot_id 只命中对应实例，无需 telegram）：
  ```
  E:\AnacondaEnvs\astrbot_env\python.exe data\plugins\group_relay\smoke_outbound.py
  ```
- **全链路**（真实 telegram 群消息→大脑、出站 curl→bot 发言、无自动回复）：需先配 telegram bot（S4.3），见上文判据。

多 bot 映射 = S4.3。
