# DevPulse — GitHub Intelligence Agent

## What It Is

A GitHub intelligence agent that answers natural language questions about GitHub repositories by combining an LLM (Groq) with live GitHub API calls through the MCP protocol.

**Example:** Ask `"What are the open issues on facebook/react?"` → agent queries GitHub API and returns current issues.

## UI Access

| Mode | Command | Description |
|---|---|---|
| **Web UI** | `python main.py ui` | Chainlit chat interface at http://localhost:8000 |
| **Web UI (port)** | `python main.py ui --port 8080` | Same, on a custom port |
| **Web UI (direct)** | `python run_ui.py --port 8000` | Launcher with the CPython 3.14 compat shim |
| **CLI Agent** | `python main.py phase3-agent` | Terminal ReAct agent with HITL |
| **CLI Multi-Agent** | `python main.py phase4-langgraph` | Terminal multi-agent with router |

> ⚠️ Always launch the web UI through `main.py ui` or `run_ui.py`, **not**
> `chainlit run ui.py` directly — see *Compatibility* below.

## Architecture (7 Components)

| Component | File | What It Does |
|---|---|---|
| CLI Entry | `main.py` | Unified CLI with 7 subcommands |
| MCP Server | `mcp_server.py` | 5 GitHub tools + 1 resource + 1 prompt |
| MCP Bridge | `mcp_bridge.py` | MCP → OpenAI schema bridge |
| Model Client | `model_client.py` | LLM wrapper (Groq default, Ollama fallback) |
| ReAct Agent | `agent_core.py` | Phase 3: LLM + tools + memory + HITL |
| Chainlit UI | `ui.py` | Interactive chat with approval buttons |
| UI Launcher | `run_ui.py` | Installs the nest-asyncio2 shim, then starts Chainlit |

## CLI Reference

```bash
python main.py phase1-server    # MCP server (standalone)
python main.py phase1-test      # MCP protocol test
python main.py phase2-check     # Groq LLM connection check
python main.py phase3-agent     # ReAct agent (terminal)
python main.py phase4-langgraph # Multi-agent (terminal)
python main.py phase5-card      # A2A card demo
python main.py ui               # Chainlit web UI (default port 8000)
python main.py ui --port 8080   # Chainlit web UI on a custom port
```

## Compatibility (CPython 3.14 — important)

The environment runs **CPython 3.14**. `chainlit` imports `nest_asyncio` and calls
`nest_asyncio.apply()` at import time. On 3.14 that call rebinds `asyncio.Task` to a
pure-Python class while `asyncio.current_task()` keeps reading the C registry, so it
returns `None` inside a running coroutine. The chain reaction
`sniffio → anyio → FastAPI` then makes Chainlit answer **HTTP 500 for every static
asset**, and the browser shows a blank page. (Upstream Chainlit issue #2952; the fix
PR #2953 was never merged.)

**Fix:** `run_ui.py` registers the maintained drop-in fork **`nest-asyncio2`**
(which fixes the 3.14 breakage) under the `nest_asyncio` module name *before*
importing `chainlit.cli`. No Chainlit internals are patched and other Python
versions are unaffected. `main.py ui` always launches through `run_ui.py`.

## Key Details

- **LLM:** Groq (`openai/gpt-oss-20b`) — key in `.env`
- **GitHub API:** Live calls via `httpx`, unauthenticated (60/hr) or token-authenticated (5000/hr)
- **Protocol:** MCP stdio transport — server runs as subprocess, stdout reserved for JSON-RPC
- **Human-in-the-loop:** Tool approval required (terminal: `y/n`, UI: Approve/Reject buttons)
- **Dependencies:** `mcp<2`, `langgraph<1`, `openai<2`, `chainlit==2.11.0`, `nest-asyncio2`, `groq`, `httpx`, `python-dotenv`

## File Structure

```
├── main.py              ← unified CLI entry point
├── __main__.py          ← enables `python -m .`
├── run_ui.py            ← UI launcher (installs nest-asyncio2 shim, starts Chainlit)
├── ui.py                ← Chainlit web UI (Phase 3 agent with HITL buttons)
├── agent_core.py        ← Phase 3: ReAct agent core (unchanged)
├── mcp_server.py        ← Phase 1: MCP server (5 tools, 1 resource, 1 prompt)
├── mcp_client_test.py   ← Phase 1: MCP protocol test
├── model_client.py      ← Phase 2: LLM client (Groq/Ollama)
├── mcp_bridge.py        ← Phase 2: MCP → OpenAI schema bridge
├── langgraph_agent.py   ← Phase 4: LangGraph multi-agent
├── a2a_card.py          ← Phase 5: Agent Card + registry
├── chainlit.md          ← Chainlit welcome screen
├── .env                 ← GROQ_API_KEY + config
├── requirements.txt     ← pinned dependencies
└── .gitignore
```
