# group_relay — Chorus ↔ AstrBot 窄消息桥

只搬字节，不含业务智能（LLM/人设/调度/记忆全在编排服务）。当前实现 **S4.1：出站**。

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

## 判据（手动，单 bot）

1. AstrBot 配一个 telegram bot 实例（记其实例 id）、把 bot 拉进一个群、发一条消息拿到该群的 `unified_msg_origin`（日志/调试可见）。
2. `curl -X POST http://127.0.0.1:9876/outbound -H 'Content-Type: application/json' -d '{"group_key":"<umo>","bot_id":"<实例id>","text":"出站测试"}'`
3. 期望：该 bot 在目标群发出"出站测试"，curl 返回 `{"ok":true,...}`。

入站 / 去重 / stop_event / 多 bot = S4.2+。
