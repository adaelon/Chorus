"""S1.4b 判据：text_json 策略能解析干净/带噪的 LLM 输出并按 schema 校验（离线）。"""

from __future__ import annotations

from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import BaseModel

from app.structured import structured_invoke


class _Out(BaseModel):
    name: str
    score: int


class _FakeModel:
    """假模型：ainvoke 恒返回预设文本，用于离线测 text_json 解析。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.seen = None

    async def astream(self, messages, config=None):  # noqa: ANN001 - 测试桩
        self.seen = messages
        yield AIMessageChunk(content=self.content)


class _FlakyModel:
    """假模型：前 `empties` 次 astream 返回空 content（模拟 kimi 只出 reasoning），之后返回有效 JSON。"""

    def __init__(self, content: str, empties: int) -> None:
        self.content = content
        self.empties = empties
        self.calls = 0

    async def astream(self, messages, config=None):  # noqa: ANN001
        self.calls += 1
        yield AIMessageChunk(content="" if self.calls <= self.empties else self.content)


async def test_text_json_retries_on_empty_content():
    """kimi 偶发空 content → 解析级重试，下次拿到有效 JSON 即成功。"""
    model = _FlakyModel('{"name":"z","score":9}', empties=2)
    out = await structured_invoke(model, [HumanMessage(content="x")], _Out, method="text_json")
    assert out.name == "z" and out.score == 9
    assert model.calls == 3  # 两次空 + 第三次成功


async def test_text_json_raises_after_persistent_empty():
    """恒空 → 重试耗尽后抛错（不静默吞掉）。"""
    import pytest

    model = _FakeModel("")  # 恒空
    with pytest.raises(Exception):
        await structured_invoke(model, [HumanMessage(content="x")], _Out, method="text_json")


async def test_text_json_parses_clean_output():
    model = _FakeModel('{"name":"a","score":3}')
    out = await structured_invoke(model, [HumanMessage(content="x")], _Out, method="text_json")
    assert out.name == "a" and out.score == 3


async def test_text_json_tolerates_fences_and_preamble():
    model = _FakeModel('思考中...\n```json\n{"name":"b","score":5}\n```\n以上。')
    out = await structured_invoke(model, [HumanMessage(content="x")], _Out, method="text_json")
    assert out.name == "b" and out.score == 5


async def test_text_json_prepends_schema_directive():
    model = _FakeModel('{"name":"c","score":1}')
    await structured_invoke(model, [HumanMessage(content="x")], _Out, method="text_json")
    # 第一条应是注入的 JSON Schema 指令（SystemMessage），原消息在其后
    assert "JSON Schema" in model.seen[0].content
    assert model.seen[-1].content == "x"
