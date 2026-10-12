"""
gitResearch -- Agent

LLM client (Groq or Ollama) + MCP bridge + ReAct loop.

Flow:
User -> LLM (Reason) -> Select Tool -> MCP Tool -> GitHub API -> LLM Response
"""

import json
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()


SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")

SYSTEM_PROMPT = (
    "You are gitResearch, an assistant that answers questions about GitHub "
    "repositories using real, live tools. Always use a tool to look up facts "
    "instead of guessing. Remember repo names mentioned earlier in the "
    "conversation so the user doesn't have to repeat them.\n\n"
    "IMPORTANT: Only call tools when you actually need information. If you "
    "already know the answer or can reason about it without external data, "
    "respond directly. Minimize unnecessary tool calls."
)


# -- LLM CLIENT ---------------------------------------------------------------


def get_client_and_model():
    """Returns (client, model_name, backend). client is async and exposes
    `await .chat.completions.create(...)` with the same signature regardless of backend."""
    backend = os.environ.get("LLM_BACKEND", "ollama" if not os.environ.get("GROQ_API_KEY")
                             else "groq").lower()

    if backend == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
        model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
        return client, model, "groq"

    # Ollama, via its OpenAI-compatible endpoint. The api_key is a
    # required-but-unchecked placeholder.
    from openai import AsyncOpenAI
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    client = AsyncOpenAI(base_url=base_url, api_key="ollama")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    return client, model, "ollama"


# -- MCP BRIDGE ---------------------------------------------------------------


@asynccontextmanager
async def mcp_session():
    """Launch mcp_server.py as a subprocess and yield a ready ClientSession.

    NOTE: env=os.environ.copy() is required -- the MCP stdio transport does
    NOT inherit the parent process's environment by default, so without this
    the server subprocess would never see GITHUB_TOKEN.
    """
    server_params = StdioServerParameters(
        command=sys.executable, args=[SERVER_PATH], env=os.environ.copy()
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def discover_openai_tools(session: ClientSession) -> list[dict]:
    """MCP list_tools(), converted into OpenAI/Groq function-calling schema.
    MCP's tool.inputSchema is already JSON Schema, so this is a thin reshape."""
    tools_result = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in tools_result.tools
    ]


async def call_mcp_tool(session: ClientSession, name: str, arguments: dict) -> str:
    """call_tool() against the live MCP server, flattened to plain text."""
    result = await session.call_tool(name, arguments=arguments)
    texts = [item.text for item in result.content if hasattr(item, "text")]
    return "\n".join(texts) if texts else "(empty result)"


# -- AGENT --------------------------------------------------------------------


class GitResearchAgent:

    def __init__(self):
        self.client, self.model, self.backend = get_client_and_model()
        self.history = []

    async def ask(self, user_input: str, prompt: tuple[str, dict] | None = None, max_loops: int = 12):
        """Run the ReAct loop (LLM -> tool call -> tool result -> LLM ...) and
        yield progress events as they happen, so a UI can render them live.

        If `prompt` is given as (name, arguments), the request sent to the LLM is
        that MCP prompt template, fetched from the server, instead of `user_input`.

        Events:
            {"type": "status", "text": ...}                  setup progress
            {"type": "prompt", "name", "text"}               an MCP prompt was loaded
            {"type": "llm_start"}                            an LLM call began
            {"type": "reasoning", "text": ...}               a chunk of model reasoning
            {"type": "token", "text": ...}                   a chunk of answer text
            {"type": "tool_start", "id", "name", "args"}     a tool call began
            {"type": "tool_end", "id", "name", "result"}     a tool call finished
            {"type": "done"}                                 final answer complete
        """
        yield {"type": "status", "text": "Connecting to GitHub tools"}
        async with mcp_session() as session:
            tools = await discover_openai_tools(session)

            if prompt:
                name, arguments = prompt
                result = await session.get_prompt(name, arguments=arguments)
                user_input = result.messages[0].content.text
                yield {"type": "prompt", "name": name, "text": user_input}

            self.history.append({"role": "user", "content": user_input})

            for _ in range(max_loops):
                messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history

                yield {"type": "llm_start"}
                stream = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    stream=True,
                )

                content = ""
                tool_calls = {}  # index -> {"id", "name", "arguments"}, assembled from deltas
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Reasoning models expose their thinking under different names
                    # depending on the backend (Groq: reasoning, others: reasoning_content).
                    reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                    if reasoning:
                        yield {"type": "reasoning", "text": reasoning}

                    if delta.content:
                        content += delta.content
                        yield {"type": "token", "text": delta.content}

                    for tc in delta.tool_calls or []:
                        call = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            call["id"] = tc.id
                        if tc.function and tc.function.name:
                            call["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            call["arguments"] += tc.function.arguments

                # No tool call needed -- final answer
                if not tool_calls:
                    self.history.append({"role": "assistant", "content": content})
                    yield {"type": "done"}
                    return

                calls = [tool_calls[i] for i in sorted(tool_calls)]
                self.history.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for c in calls
                    ],
                })

                for call in calls:
                    try:
                        args = json.loads(call["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    yield {"type": "tool_start", "id": call["id"], "name": call["name"], "args": args}
                    result_text = await call_mcp_tool(session, call["name"], args)
                    yield {"type": "tool_end", "id": call["id"], "name": call["name"], "result": result_text}

                    self.history.append({
                        "role": "tool", "tool_call_id": call["id"], "content": result_text,
                    })

        yield {"type": "token", "text": "I wasn't able to resolve this within the allotted reasoning steps."}
        yield {"type": "done"}
