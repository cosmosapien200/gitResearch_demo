# gitResearch

A GitHub research agent with a Chainlit chat UI. It answers the everyday developer question
**"Should I use this repo?"** with live data: an LLM (Groq or Ollama) plans which GitHub tools
to call, calls them through a local MCP server, and writes up the result.

## Features

| Feature | How to use it | What happens |
|---|---|---|
| **Chat** | Ask anything, e.g. *"latest release of vercel/next.js?"* | The agent picks and calls tools itself |
| **🩺 Health check** | `/Health fastapi/fastapi` | 6 tools → report with a 🟢/🟡/🔴 verdict |
| **⚖️ Compare** | `/Compare fastapi/fastapi vs pallets/flask` | Health facts for both → side-by-side table |
| **🗂️ Issue triage** | `/Triage microsoft/vscode` | Open issues → type, priority, what to fix first |
| **📄 Download** | Click the `.md` file under any report | Saves the report as Markdown |
| **Live progress** | — | Each tool call shows as a step; the answer streams in |

The empty chat screen has one-click starter buttons for each feature.

## How it works

```
Chainlit UI (app.py) -> Agent ReAct loop (agent.py) -> MCP server (mcp_server.py) -> GitHub REST API
```

The agent launches `mcp_server.py` as a stdio subprocess and discovers what it offers:

**Tools** — live GitHub lookups the LLM can call:

| Tool | What it returns |
|---|---|
| `search_repositories` | Top 5 repos for a query, by stars |
| `get_repo_details` | Stars, forks, language, license, last push |
| `get_readme` | The README text (what the project does) |
| `get_recent_commits` | Latest commits with how long ago each was made |
| `list_open_issues` | Open issues (not PRs) with labels, age and description |
| `list_contributors` | Top contributors by commit count |
| `get_latest_release` | Latest release tag, date and notes |

**Prompts** — workflow templates behind the report commands. `/Health` fetches the
`repo_health_check` prompt from the server, fills in the repo, and sends it to the LLM. The
prompt lists which tools to use, the 🟢/🟡/🔴 rules, and the exact report format; the LLM
then decides the tool calls itself. `/Compare` uses `compare_repos` and `/Triage` uses
`triage_issues`.

Adding a tool or prompt to `mcp_server.py` makes it available to the agent with no change to
the agent code, because tools are discovered at runtime.

## Setup

Requires Python 3.10–3.13.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env          # then fill in your settings
```

For Groq, set `LLM_BACKEND=groq` and `GROQ_API_KEY`. For Ollama, install
[Ollama](https://ollama.com), then `ollama pull <OLLAMA_MODEL>`. A `GITHUB_TOKEN` is optional
but raises the GitHub rate limit from 60 to 5,000 requests/hour; a health check uses about 6
requests and a comparison about 12.

## Run

```bash
chainlit run app.py
```

Then open http://localhost:8000. Add `-w` to reload automatically when the code changes.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run offline: GitHub, the MCP session and the LLM are replaced with fakes.

## Project layout

```
├── app.py                  Chainlit UI: commands, starters, live steps, report download
├── agent.py                LLM client, MCP bridge, streaming ReAct loop
├── mcp_server.py           MCP server: GitHub tools, resource, report prompts
├── chainlit.md             In-app Readme
├── .chainlit/config.toml   Chainlit settings (app name)
├── tests/                  Offline unit tests (pytest)
├── requirements.txt
└── .env.example
```
