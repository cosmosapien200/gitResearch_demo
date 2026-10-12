import os
import sys
from dotenv import load_dotenv

load_dotenv()


def get_client_and_model():
    """Returns (client, model_name, backend). client exposes .chat.completions.create(...)
    with the same signature regardless of backend."""
    backend = os.environ.get("LLM_BACKEND", "ollama" if not os.environ.get("GROQ_API_KEY")
                             else "groq").lower()

    if backend == "groq":
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
        return client, model, "groq"

    # -- Ollama, via its OpenAI-compatible endpoint --------------------------
    # Same base_url pattern used by the AutoGen/LangGraph labs' config_list:
    # http://localhost:11434/v1 -- the api_key is a required-but-unchecked
    # placeholder, exactly like in those labs.
    from openai import OpenAI
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    client = OpenAI(base_url=base_url, api_key="ollama")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    return client, model, "ollama"


def check_connection(client, model, backend):
    """Friendly pre-flight check, same idea as the MCP capstone's call_qwen
    test -- fail with a clear, backend-specific message instead of a raw
    connection traceback."""
    try:
        client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "Say OK in one word"}],
            max_tokens=5
        )
        print("after api call")
        return True
    except Exception as e:
        if backend == "ollama":
            print(f"\nCould not reach Ollama at the configured base_url.", file=sys.stderr)
            print(f" 1) Is it running? -> ollama serve", file=sys.stderr)
            print(f" 2) Is the model pulled? -> ollama pull {model}", file=sys.stderr)
        else:
            print(f"\nCould not reach Groq.", file=sys.stderr)
            print(f" Check that GROQ_API_KEY is set correctly.", file=sys.stderr)
        print(f" Underlying error: {e}\n", file=sys.stderr)
        return False
