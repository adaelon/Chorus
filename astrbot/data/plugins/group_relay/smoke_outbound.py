"""S4.3 出站精确路由 smoke（端到端，需真实 astrbot 环境，无需 telegram）。

跑法（conda env）：
    cd E:\\allwork\\download\\agent\\Chorus\\astrbot
    E:\\AnacondaEnvs\\astrbot_env\\python.exe data\\plugins\\group_relay\\smoke_outbound.py

做什么：加载真实插件 GroupRelay，注册两个假 platform 实例（bot_a / bot_b），真实
起 aiohttp /outbound 桥，POST {bot_id: bot_a} → 验证：
  ① 只有 bot_a 的实例收到 send_by_session（bot_b 没有）= 精确路由到对应 bot
  ② 会话平台段被换成 bot_id（群里显示为该 bot 发言）
  ③ 未知 bot_id → 404
对应 S4.3 判据"大脑发 /outbound 能精确路由到对应 bot"的确定性部分（真 telegram 多 bot
RUNNING 需配 token 手动验）。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # astrbot 根入 path

BRIDGE_PORT = 9878  # 避开真实 AstrBot 的 9876


class _FakePlatform:
    def __init__(self, pid: str):
        self._pid = pid
        self.sent = []

    def meta(self):
        class _M:
            id = self._pid

        m = _M()
        m.id = self._pid
        return m

    async def send_by_session(self, session, chain):
        self.sent.append((session, chain))


class _FakeContext:
    def __init__(self, insts: dict):
        self._insts = insts

    def get_platform_inst(self, platform_id):
        return self._insts.get(platform_id)


async def main() -> int:
    bot_a = _FakePlatform("bot_a")
    bot_b = _FakePlatform("bot_b")
    ctx = _FakeContext({"bot_a": bot_a, "bot_b": bot_b})

    mod = importlib.import_module("data.plugins.group_relay.main")
    plugin = mod.GroupRelay(ctx, config={"bridge_port": BRIDGE_PORT})
    await plugin.initialize()

    base = f"http://127.0.0.1:{BRIDGE_PORT}/outbound"
    ok = True
    try:
        async with aiohttp.ClientSession() as s:
            # ① 路由到 bot_a
            async with s.post(
                base,
                json={"group_key": "telegram_x:GroupMessage:42", "bot_id": "bot_a", "text": "你好"},
            ) as r:
                body = await r.json()
            if r.status == 200 and body.get("ok") and len(bot_a.sent) == 1 and len(bot_b.sent) == 0:
                print("PASS 精确路由：bot_a 收到 1 条，bot_b 收到 0 条")
            else:
                print(f"FAIL 精确路由：status={r.status} body={body} a={len(bot_a.sent)} b={len(bot_b.sent)}")
                ok = False
            # ② 会话平台段 = bot_id
            if bot_a.sent and str(bot_a.sent[0][0]).startswith("bot_a:"):
                print(f"PASS 身份：会话 = {bot_a.sent[0][0]}（以 bot_a 发言）")
            else:
                print(f"FAIL 身份：{bot_a.sent[:1]}")
                ok = False
            # ③ 未知 bot → 404
            async with s.post(
                base, json={"group_key": "p:GroupMessage:1", "bot_id": "ghost", "text": "x"}
            ) as r2:
                if r2.status == 404:
                    print("PASS 未知 bot：404")
                else:
                    print(f"FAIL 未知 bot：status={r2.status}")
                    ok = False

        print("\nSMOKE", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        await plugin.terminate()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
