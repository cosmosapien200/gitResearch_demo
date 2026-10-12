"""
DevPulse Chainlit UI — Interactive chat interface for the ReAct agent.

Integrates with the existing Phase 3 agent (agent_core.py) while replacing
terminal-based human-in-the-loop approval with direct tool execution.

Design notes
------------
* ``ChainlitAgent`` subclasses :class:`agent_core.DevPulseAgent` and reuses the
  exact same ReAct loop (LLM -> tool call -> tool result -> LLM ...), MCP
  session and conversation memory.  Tool calls execute automatically — no
  approval prompts.
* The synchronous Groq client is driven through ``asyncio.to_thread`` so a
  long inference does not block the event loop (and therefore other sessions).
* Only the final answer is shown to the user as a new message. No intermediate
  steps or placeholders are displayed.
"""

import asyncio
import json
import logging

import chainlit as cl

from agent_core import DevPulseAgent, SYSTEM_PROMPT
from mcp_bridge import mcp_session, discover_openai_tools, call_mcp_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chainlit-friendly wrapper around DevPulseAgent
# ---------------------------------------------------------------------------


class ChainlitAgent(DevPulseAgent):
    """Extends DevPulseAgent to execute tool calls directly (no approval)."""

    async def ask(self, user_input: str, max_loops: int = 4) -> str:
        """Run the ReAct loop with automatic tool execution.

        Preserves the original logic (MCP session, ReAct loop, conversation
        memory, tool execution) — tool calls run without human approval.
        """
        self.history.append({"role": "user", "content": user_input})

        try:
            async with mcp_session() as session:
                tools = await discover_openai_tools(session)

                for loop_num in range(max_loops):
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history

                    # --- LLM inference (off the event loop) ---
                    response = await asyncio.to_thread(
                        self.client.chat.completions.create,
                        model=self.model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                    )

                    msg = response.choices[0].message
                    finish_reason = response.choices[0].finish_reason

                    # No tool call needed — final answer
                    if finish_reason != "tool_calls" or not msg.tool_calls:
                        self.history.append({"role": "assistant", "content": msg.content})
                        return msg.content

                    # Store the assistant message (including tool calls)
                    self.history.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
                    })

                    # --- Execute each tool call directly ---
                    for tool_call in msg.tool_calls:
                        try:
                            args = json.loads(tool_call.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}

                        result_text = await call_mcp_tool(
                            session, tool_call.function.name, args
                        )

                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text,
                        })

        except Exception as exc:
            logger.exception("Agent ask() failed")
            return f"Error during processing: {exc}"

        return "I wasn't able to resolve this within the allotted reasoning steps."


# ---------------------------------------------------------------------------
# Chainlit lifecycle hooks
# ---------------------------------------------------------------------------


@cl.on_chat_start
async def on_chat_start():
    """Initialize a fresh agent (and conversation memory) for this session."""
    try:
        agent = ChainlitAgent()
    except Exception as exc:
        logger.exception("Failed to initialize agent")
        await cl.Message(
            content=(
                "⚠️ Could not initialize the agent.\n\n"
                f"`{exc}`\n\n"
                "Check your `.env` — `GROQ_API_KEY` and `LLM_BACKEND` must be set."
            )
        ).send()
        return

    welcome = (
        f"## Welcome to DevPulse 🚀\n\n"
        f"Your GitHub Intelligence Agent is ready.\n\n"
        f"**Model:** `{agent.model}`\n"
        f"**Backend:** `{agent.backend}`\n\n"
        f"---\n\n"
        f"**Ask me about any GitHub repository:**\n"
        f"- *\"What are the stars on microsoft/autogen?\"*\n"
        f"- *\"Show me open issues on facebook/react\"*\n"
        f"- *\"Who are the top contributors to langchain-ai/langchain?\"*\n"
        f"- *\"What's the latest release of vercel/next.js?\"*\n\n"
        f"Tool calls execute automatically. "
        f"Type `quit` to end the chat."
    )
    await cl.Message(content=welcome).send()
    cl.user_session.set("agent", agent)


@cl.on_stop
async def on_stop():
    """Handle the user cancelling the current task (stop button)."""
    logger.info("User stopped the current task")
    await cl.Message(content="⏹️ Stopped the current task.", author="DevPulse").send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle an incoming user message and run the agent loop."""
    agent: ChainlitAgent = cl.user_session.get("agent")
    if not agent:
        await cl.Message(content="Agent not initialized. Please start a new chat.").send()
        return

    user_input = message.content.strip()

    if user_input.lower() in {"quit", "exit"}:
        await cl.Message(
            content="Chat ended. Start a new conversation to continue.",
            author="DevPulse",
        ).send()
        return

    # Run the agent and send the final answer as a new message.
    try:
        answer = await agent.ask(user_input)
        await cl.Message(content=answer or "(no response)", author="DevPulse").send()
    except Exception as exc:
        logger.exception("Message handling failed")
        await cl.Message(
            content=f"❌ Error: {exc}", author="DevPulse"
        ).send()
