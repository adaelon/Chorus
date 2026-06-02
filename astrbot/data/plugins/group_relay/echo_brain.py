"""验证用"假大脑"：收 group_relay 的入站 POST 并打印（看转发的 InboundMsg）。

跑法（任意 python，无第三方依赖）：
    python data\plugins\group_relay\echo_brain.py            # 默认 8900
    python data\plugins\group_relay\echo_brain.py 8901       # 指定端口

用途：联调 S4.4 前，先用它当大脑——在 telegram 群发消息，这里会打印 InboundMsg
（含 group_key / sender / text / native_msg_id），同时能确认 N bot 同条只转一次、
AstrBot 自身不自动回复。注意：插件默认 brain_url = http://127.0.0.1:8900，所以用
8900 时先停掉真的 orchestrator（或把插件 brain_url 指到本脚本端口）。
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_n = 0


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global _n
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            body = raw.decode("utf-8", "replace")
        _n += 1
        print(f"\n[#{_n} {self.path}] {json.dumps(body, ensure_ascii=False)}", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):  # 静音默认访问日志
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
    print(f"echo_brain 监听 http://127.0.0.1:{port}/inbound（Ctrl+C 退出）", flush=True)
    HTTPServer(("127.0.0.1", port), _Handler).serve_forever()
