"""
DevPulse — Unified CLI Entry Point

Usage:
    python main.py phase1-server       Run the MCP server
    python main.py phase1-test         Run the MCP client test
    python main.py phase2-check        Check LLM connection
    python main.py phase3-agent        Run the ReAct agent (interactive)
    python main.py phase4-langgraph    Run the LangGraph multi-agent (interactive)
    python main.py phase5-card         Run the A2A Agent Card demo
    python main.py ui                  Launch Chainlit web UI (http://localhost:8000)
    python main.py ui --port 8080      Launch Chainlit web UI on a custom port
"""

import argparse
import subprocess
import sys
import os


SCRIPTS = {
    "phase1-server": "mcp_server.py",
    "phase1-test": "mcp_client_test.py",
    "phase2-check": None,
    "phase3-agent": "agent_core.py",
    "phase4-langgraph": "langgraph_agent.py",
    "phase5-card": "a2a_card.py",
}


def phase2_check():
    """Run the Phase 2 LLM connection check."""
    code = """
import os
from dotenv import load_dotenv
load_dotenv()
from model_client import get_client_and_model, check_connection
client, model, backend = get_client_and_model()
print(f"Selected backend : {backend}")
print(f"Model            : {model}")
print(f"API Key set      : {'Yes' if os.environ.get('GROQ_API_KEY') else 'No'}")
print()
check_connection(client, model, backend)
"""
    exec(compile(code, "<phase2_check>", "exec"))


def main():
    parser = argparse.ArgumentParser(
        prog="main",
        description="DevPulse — GitHub Intelligence Agent (MCP + Agent + LangGraph + A2A)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available phases")

    for cmd in sorted(SCRIPTS):
        subparsers.add_parser(cmd, help=f"Run {cmd.replace('-', ' ').title()}")

    # UI is a special command, not a standalone script
    ui_parser = subparsers.add_parser("ui", help="Launch Chainlit web UI (http://localhost:8000)")
    ui_parser.add_argument("--port", type=int, default=8000, help="Port to serve on (default: 8000)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Phase 2 is special — it's an inline check, not a separate script
    if args.command == "phase2-check":
        phase2_check()
        return

    # UI mode — launches the Chainlit web interface through run_ui.py, which
    # installs the nest-asyncio2 compatibility shim before importing Chainlit
    # (works around the CPython 3.14 / nest_asyncio bug — see run_ui.py).
    if args.command == "ui":
        base_dir = os.path.dirname(os.path.abspath(__file__))
        launcher = os.path.join(base_dir, "run_ui.py")
        result = subprocess.run(
            [sys.executable, launcher, "--port", str(args.port)],
            cwd=base_dir,
        )
        sys.exit(result.returncode)

    script = SCRIPTS[args.command]
    result = subprocess.run([sys.executable, script], cwd=os.path.dirname(os.path.abspath(__file__)))
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
