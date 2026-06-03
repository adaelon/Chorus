"""S1.2 判据：retry 包住瞬时断连；真实后端 smoke（默认跳过，需 CHORUS_RUN_SMOKE=1）。"""

from __future__ import annotations

import os

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.llm import make_chat_model, robust_invoke


class _FlakyModel:
    """前 fail_times 次 invoke 抛 APIConnectionError，之后返回正常 AIMessage。"""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def invoke(self, messages):  # noqa: ANN001 - 仅测试桩
        self.calls += 1
        if self.calls <= self.fail_times:
            raise openai.APIConnectionError(request=httpx.Request("POST", "http://test/v1"))
        return AIMessage(content="ok")


def test_retry_recovers_from_transient_disconnect():
    model = _FlakyModel(fail_times=1)
    out = robust_invoke(model, [HumanMessage(content="hi")], attempts=3, wait_initial=0.0)
    assert out.content == "ok"
    assert model.calls == 2  # 首次失败 + 第二次成功


def test_retry_gives_up_and_reraises():
    model = _FlakyModel(fail_times=10)
    with pytest.raises(openai.APIConnectionError):
        robust_invoke(model, [HumanMessage(content="hi")], attempts=3, wait_initial=0.0)
    assert model.calls == 3  # 用满 attempts 后抛原始异常


@pytest.mark.skipif(
    not os.environ.get("CHORUS_RUN_SMOKE"),
    reason="设置 CHORUS_RUN_SMOKE=1 跑真实后端 smoke",
)
def test_smoke_real_backend():
    model = make_chat_model()
    out = robust_invoke(model, [HumanMessage(content="Reply with exactly: pong")])
    assert isinstance(out.content, str) and out.content.strip()
