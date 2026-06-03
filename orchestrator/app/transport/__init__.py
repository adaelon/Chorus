"""传输/运行时层（§6.12）：transport 无关的事件运行时 + telegram relay 驱动 + 出站客户端。

再导出公共 API，外部按 `from ..transport import X` 用。
"""

from __future__ import annotations

from .outbound_client import OutboundClient
from .relay import RelayDriver
from .runtime import iter_events

__all__ = ["iter_events", "RelayDriver", "OutboundClient"]
