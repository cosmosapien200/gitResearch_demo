"""Tests for the agent loop. The LLM and the MCP server are replaced with fakes,
so these run offline and check only the loop logic."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import agent


def chunk(content=None, tool_call=None):
    """Build one streamed chunk in the same shape the OpenAI/Groq SDKs return."""
    delta = SimpleNamespace(content=content, tool_calls=[tool_call] if tool_call else None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def tool_call_chunk(index, id=None, name=None, arguments=None):
    return chunk(tool_call=SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)))


class FakeLLM:
    """Plays back one list of chunks per call to chat.completions.create()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self.responses.pop(0)

        async def stream():
            for c in chunks:
                yield c
        return stream()


def make_agent(monkeypatch, responses, tool_result="tool output"):
    tool_calls = []

    @asynccontextmanager
    async def fake_session():
        yield "session"

    async def fake_discover(session):
        return [{"type": "function", "function": {"name": "get_repo_details"}}]

    async def fake_call(session, name, args):
        tool_calls.append((name, args))
        return tool_result

    monkeypatch.setattr(agent, "mcp_session", fake_session)
    monkeypatch.setattr(agent, "discover_openai_tools", fake_discover)
    monkeypatch.setattr(agent, "call_mcp_tool", fake_call)

    a = agent.GitResearchAgent.__new__(agent.GitResearchAgent)
    a.client, a.model, a.backend, a.history = FakeLLM(responses), "fake", "test", []
    return a, tool_calls


def run(a, text):
    async def collect():
        return [e async for e in a.ask(text)]
    return asyncio.run(collect())


def test_direct_answer_without_tools(monkeypatch):
    a, tool_calls = make_agent(monkeypatch, [[chunk("Hello"), chunk(" there")]])

    events = run(a, "hi")

    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "Hello there"
    assert events[-1]["type"] == "done"
    assert tool_calls == []
    assert a.history[-1] == {"role": "assistant", "content": "Hello there"}


def test_tool_call_then_answer(monkeypatch):
    # The tool call arrives split over several chunks, like a real stream
    first = [
        tool_call_chunk(0, id="call_1", name="get_repo_details", arguments='{"owner": "fa'),
        tool_call_chunk(0, arguments='stapi", "repo": "fastapi"}'),
    ]
    second = [chunk("It has 100k stars.")]
    a, tool_calls = make_agent(monkeypatch, [first, second], tool_result="100,000 stars")

    events = run(a, "stars on fastapi/fastapi?")

    assert tool_calls == [("get_repo_details", {"owner": "fastapi", "repo": "fastapi"})]
    types = [e["type"] for e in events]
    assert types.index("tool_start") < types.index("tool_end") < types.index("token")
    # the tool result must be in the history the LLM sees on its second call
    assert {"role": "tool", "tool_call_id": "call_1", "content": "100,000 stars"} in a.history


def test_memory_across_turns(monkeypatch):
    a, _ = make_agent(monkeypatch, [[chunk("First answer")], [chunk("Second answer")]])

    run(a, "question one")
    run(a, "question two")

    second_call_messages = a.client.calls[1]["messages"]
    contents = [m["content"] for m in second_call_messages]
    assert "question one" in contents and "First answer" in contents


def test_loop_limit(monkeypatch):
    # A model that only ever asks for tools must not loop forever
    always_tool = [tool_call_chunk(0, id="c", name="get_repo_details", arguments="{}")]
    a, tool_calls = make_agent(monkeypatch, [always_tool] * 3)

    async def collect():
        return [e async for e in a.ask("loop", max_loops=3)]
    events = asyncio.run(collect())

    assert len(tool_calls) == 3
    assert "wasn't able to resolve" in events[-2]["text"]
