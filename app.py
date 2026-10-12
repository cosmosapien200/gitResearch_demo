"""
gitResearch -- Chainlit UI

Run with:
    chainlit run app.py
"""

import logging
import re

import chainlit as cl
from chainlit.utils import utc_now

from agent import GitResearchAgent

logger = logging.getLogger(__name__)


# -- REPORT COMMANDS ------------------------------------------------------------
# Each command in the chat box runs one of the MCP server's prompt templates.
# command id -> (MCP prompt name, how many repos it needs, report file suffix)

COMMANDS = {
    "Health": ("repo_health_check", 1, "health-report"),
    "Compare": ("compare_repos", 2, "comparison"),
    "Triage": ("triage_issues", 1, "issue-triage"),
}

COMMAND_BUTTONS = [
    {"id": "Health", "icon": "heart-pulse",
     "description": "Health check a repo, e.g. fastapi/fastapi"},
    {"id": "Compare", "icon": "git-compare",
     "description": "Compare two repos, e.g. fastapi/fastapi vs pallets/flask"},
    {"id": "Triage", "icon": "list-checks",
     "description": "Triage a repo's open issues, e.g. microsoft/vscode"},
]

# Matches "owner/repo", also inside a pasted github.com URL.
REPO_PATTERN = re.compile(r"(?:github\.com/)?([\w.-]+)/([\w.-]+)")


def parse_repos(text: str) -> list[tuple[str, str]]:
    """'fastapi/fastapi vs https://github.com/pallets/flask' -> [('fastapi', 'fastapi'), ('pallets', 'flask')]"""
    return [(owner, repo.removesuffix(".git")) for owner, repo in REPO_PATTERN.findall(text)]


def prompt_arguments(prompt_name: str, repos: list[tuple[str, str]]) -> dict:
    if prompt_name == "compare_repos":
        return {"repo_a": "/".join(repos[0]), "repo_b": "/".join(repos[1])}
    owner, repo = repos[0]
    return {"owner": owner, "repo": repo}


# -- CHAINLIT LIFECYCLE HOOKS -------------------------------------------------------


@cl.set_starters
async def set_starters():
    """One-click examples on the empty chat screen."""
    return [
        cl.Starter(label="Health check FastAPI", message="fastapi/fastapi", command="Health"),
        cl.Starter(label="Compare FastAPI vs Flask", message="fastapi/fastapi vs pallets/flask",
                   command="Compare"),
        cl.Starter(label="Triage open issues in VS Code", message="microsoft/vscode",
                   command="Triage"),
        cl.Starter(label="Latest release of Next.js",
                   message="What's the latest release of vercel/next.js?"),
    ]


@cl.on_chat_start
async def on_chat_start():
    """Initialize a fresh agent (and conversation memory) for this session."""
    try:
        agent = GitResearchAgent()
    except Exception as exc:
        logger.exception("Failed to initialize agent")
        await cl.Message(
            content=(
                "⚠️ Could not initialize the agent.\n\n"
                f"`{exc}`\n\n"
                "Check your `.env` — `LLM_BACKEND` and the matching backend settings must be set."
            )
        ).send()
        return

    logger.info("Agent ready (backend: %s, model: %s)", agent.backend, agent.model)
    cl.user_session.set("agent", agent)
    await cl.context.emitter.set_commands(COMMAND_BUTTONS)


@cl.on_stop
async def on_stop():
    """Handle the user cancelling the current task (stop button)."""
    logger.info("User stopped the current task")
    await cl.Message(content="⏹️ Stopped the current task.", author="gitResearch").send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle an incoming user message and run the agent loop."""
    agent: GitResearchAgent = cl.user_session.get("agent")
    if not agent:
        await cl.Message(content="Agent not initialized. Please start a new chat.").send()
        return

    # A report command runs an MCP prompt template; plain chat goes to the agent as-is.
    prompt = None
    report_name = None
    if message.command in COMMANDS:
        prompt_name, repos_needed, suffix = COMMANDS[message.command]
        repos = parse_repos(message.content)
        if len(repos) < repos_needed:
            example = "owner/repo vs owner/repo" if repos_needed == 2 else "owner/repo"
            await cl.Message(
                content=f"**{message.command}** needs {repos_needed} repo(s), written like `{example}`.",
                author="gitResearch",
            ).send()
            return
        prompt = (prompt_name, prompt_arguments(prompt_name, repos))
        report_name = "-vs-".join(r for _, r in repos[:repos_needed]) + f"-{suffix}.md"

    # Created on the first answer token, so it's ordered after the steps above it.
    answer = None
    progress = None  # the currently running status/thinking step, if any
    tool_steps = {}
    # Steps created outside `async with` don't pick up a parent; nest them under the
    # same run as the answer message so Chainlit renders them in order, above it.
    run_id = cl.context.current_step.id

    async def close_progress():
        """Finish the open status/thinking step; drop it if nothing was streamed into it."""
        nonlocal progress
        if progress is None:
            return
        if progress.output:
            progress.end = utc_now()
            await progress.update()
        else:
            await progress.remove()
        progress = None

    try:
        async for event in agent.ask(message.content.strip(), prompt=prompt):
            kind = event["type"]

            if kind == "status":
                await close_progress()
                progress = cl.Step(name=event["text"], type="run", parent_id=run_id, show_input=False)
                await progress.send()

            elif kind == "prompt":
                await close_progress()
                step = cl.Step(name=f"MCP prompt: {event['name']}", type="run",
                               parent_id=run_id, show_input=False)
                step.output = event["text"]
                step.end = utc_now()
                await step.send()

            elif kind == "llm_start":
                await close_progress()
                progress = cl.Step(name="Thinking", type="llm", parent_id=run_id, show_input=False)
                await progress.send()

            elif kind == "reasoning":
                if progress is not None:
                    await progress.stream_token(event["text"])

            elif kind == "token":
                await close_progress()
                if answer is None:
                    answer = cl.Message(content="", author="gitResearch")
                await answer.stream_token(event["text"])

            elif kind == "tool_start":
                await close_progress()
                # Text the model wrote before deciding to call a tool is narration,
                # not the answer -- start the answer fresh after the tools run.
                if answer is not None:
                    await answer.remove()
                    answer = None
                step = cl.Step(name=event["name"], type="tool", parent_id=run_id)
                step.input = event["args"]
                await step.send()
                tool_steps[event["id"]] = step

            elif kind == "tool_end":
                step = tool_steps.pop(event["id"])
                step.output = event["result"]
                step.end = utc_now()
                await step.update()

            elif kind == "done":
                await close_progress()
                if answer is None:
                    await cl.Message(content="(no response)", author="gitResearch").send()
                    return
                # Reports get a downloadable Markdown copy attached under the answer.
                if report_name:
                    answer.elements = [cl.File(name=report_name, content=answer.content.encode("utf-8"),
                                               mime="text/markdown", display="inline")]
                await answer.update()

    except Exception as exc:
        logger.exception("Message handling failed")
        await close_progress()
        await cl.Message(content=f"❌ Error: {exc}", author="gitResearch").send()
