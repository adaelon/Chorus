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
