"""S4.2 入站 smoke（端到端，需真实 astrbot 环境）。

跑法（conda env，从 Chorus/astrbot 目录）：
    cd E:\\allwork\\download\\agent\\Chorus\\astrbot
    E:\\AnacondaEnvs\\astrbot_env\\python.exe data\\plugins\\group_relay\\smoke_inbound.py

做什么：起一个假"大脑"(aiohttp 收 POST /inbound 记录)，加载真实插件 GroupRelay，
用仿真 group event 驱动真实入站钩子 on_group_message，验证：
  ① 人类消息被真实 POST 到大脑（aiohttp 实跑）
  ② 同 (group_key, native_msg_id) 第二次不再转发（去重）
  ③ 每条都调用了 event.stop_event()（防 AstrBot 自动回复）
不依赖 telegram/真实平台——平台接入后的真实群消息→无自动回复，另在 AstrBot 进程手动验。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

from aiohttp import web

# astrbot 根（data/plugins/group_relay/smoke_inbound.py → 上溯 4 层）入 path，
# 使 `data.plugins.group_relay.main` 可按包导入（不依赖运行 cwd）。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BRAIN_PORT = 8911
BRIDGE_PORT = 9877  # 避开 AstrBot 可能已占的 9876


class _FakeMsgObj:
    def __init__(self, message_id: str, ts: int):
        self.message_id = message_id
        self.timestamp = ts


class _FakeEvent:
    """仿真 AstrMessageEvent：只实现 on_group_message 用到的访问器。"""

    def __init__(self, message_id: str):
        self.unified_msg_origin = "telegram_test:GroupMessage:42"
        self.message_obj = _FakeMsgObj(message_id, 1717300000)
        self.stopped = False

    def get_sender_id(self):
        return "user1"

    def get_self_id(self):
        return "botself"

    def get_message_str(self):
        return "在吗"

    def get_sender_name(self):
        return "小明"

    def get_platform_name(self):
        return "telegram"

    def stop_event(self):
        self.stopped = True


class _FakeContext:
    def get_platform_inst(self, platform_id):
        return None  # 入站 smoke 不用出站


async def main() -> int:
    received: list[dict] = []

    async def _inbound(request):
        received.append(await request.json())
        return web.json_response({"ok": True})

    brain = web.Application()
    brain.router.add_post("/inbound", _inbound)
    brain_runner = web.AppRunner(brain)
    await brain_runner.setup()
    await web.TCPSite(brain_runner, "127.0.0.1", BRAIN_PORT).start()

    mod = importlib.import_module("data.plugins.group_relay.main")
    plugin = mod.GroupRelay(
        _FakeContext(),
        config={"bridge_port": BRIDGE_PORT, "brain_url": f"http://127.0.0.1:{BRAIN_PORT}"},
    )
    await plugin.initialize()

    try:
        e1 = _FakeEvent("m1")
        e1dup = _FakeEvent("m1")  # 同一条（N bot 收到的副本）
        e2 = _FakeEvent("m2")
        for e in (e1, e1dup, e2):
            await plugin.on_group_message(e)
        await asyncio.sleep(0.1)  # 等 POST 落地

        ok = True
        # ① 转发：m1 + m2 各一次（去重后），m1 重复不再发
        ids = [m["native_msg_id"] for m in received]
        if sorted(ids) != ["m1", "m2"]:
            print(f"FAIL 转发去重：期望 [m1,m2]，实际 {ids}")
            ok = False
        else:
            print(f"PASS 转发去重：大脑收到 {ids}（同 m1 两次只转一次）")
        # ② 规范化字段
        if received and received[0].get("sender_kind") == "human" and received[0].get("text") == "在吗":
            print("PASS 规范化：sender_kind=human, text=在吗")
        else:
            print(f"FAIL 规范化：{received[:1]}")
            ok = False
        # ③ 三条都被 stop_event 截断
        if e1.stopped and e1dup.stopped and e2.stopped:
            print("PASS stop_event：三条都截断（防自动回复）")
        else:
            print(f"FAIL stop_event：{[e1.stopped, e1dup.stopped, e2.stopped]}")
            ok = False

        print("\nSMOKE", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        await plugin.terminate()
        await brain_runner.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
