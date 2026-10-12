"""
DevPulse — Chainlit UI launcher.

Why this file exists
--------------------
`chainlit run ui.py` imports ``chainlit.cli``, which calls
``nest_asyncio.apply()`` at import time.  On CPython >= 3.14 that call
rebinds ``asyncio.Task`` / ``asyncio.Future`` to their pure-Python
implementations while ``asyncio.current_task()`` keeps reading the C-level
task registry (which the pure-Python classes never populate).  As a result
``asyncio.current_task()`` returns ``None`` inside a running coroutine, which
breaks ``sniffio`` -> ``anyio`` -> FastAPI's threadpool and makes Chainlit
answer HTTP 500 for every static asset (the browser shows a blank page).

That is upstream Chainlit issue #2952.  ``nest-asyncio2`` is a maintained,
API-compatible fork that fixes the 3.14 breakage.

Fix: register ``nest_asyncio2`` under the ``nest_asyncio`` module name
*before* ``chainlit.cli`` is imported, so Chainlit's own ``import
nest_asyncio`` resolves to the fixed implementation.  No Chainlit internals
are patched and the behaviour on older Pythons is unchanged.

Run it with:
    python run_ui.py [--host HOST] [--port PORT]
"""

import os
import sys


def _install_nest_asyncio_compat() -> None:
    """Make ``import nest_asyncio`` resolve to the 3.14-safe fork."""
    try:
        import nest_asyncio2
    except ImportError:
        # Fork not installed. On <3.14 the stock nest_asyncio is fine; on >=3.14
        # Chainlit's static assets will 500, so warn loudly.
        if sys.version_info >= (3, 14):
            print(
                "WARNING: nest-asyncio2 is not installed. On Python "
                f"{sys.version_info.major}.{sys.version_info.minor} the Chainlit UI "
                "will fail to load (HTTP 500 on static assets).\n"
                "         Install it with:  pip install nest-asyncio2",
                file=sys.stderr,
            )
        return
    sys.modules.setdefault("nest_asyncio", nest_asyncio2)


_install_nest_asyncio_compat()

# Imported *after* the shim so Chainlit picks up the patched module.
from chainlit.cli import cli  # noqa: E402


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Launch the DevPulse Chainlit UI")
    parser.add_argument("--host", default=None, help="Bind host (default: Chainlit default)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    ui_path = os.path.join(here, "ui.py")

    chainlit_args = ["run", ui_path, "--port", str(args.port)]
    if args.host:
        chainlit_args += ["--host", args.host]
    # Let `chainlit run` accept anything else the user passed through.
    cli.main(args=chainlit_args, prog_name="chainlit", standalone_mode=True)


if __name__ == "__main__":
    main()
