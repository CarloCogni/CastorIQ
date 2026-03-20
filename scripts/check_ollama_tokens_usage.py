"""
Probe Ollama API to find where context window size lives.
Run with: python probe_ollama_context.py
Requires: ollama Python package (pip install ollama)
"""

import json
import sys

try:
    import ollama
except ImportError:
    print("ERROR: ollama package not installed. Run: pip install ollama")
    sys.exit(1)


def probe_model(model_name: str):
    print(f"\n{'='*60}")
    print(f"MODEL: {model_name}")
    print(f"{'='*60}")

    client = ollama.Client()  # defaults to localhost:11434

    try:
        info = client.show(model_name)
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    # 1) Top-level keys
    print(f"\n  Top-level keys: {list(info.keys()) if isinstance(info, dict) else type(info)}")

    # 2) Check 'modelinfo' / 'model_info' for context_length keys
    for key in ["modelinfo", "model_info"]:
        if key in info and isinstance(info[key], dict):
            print(f"\n  --- {key} ---")
            ctx_keys = {k: v for k, v in info[key].items() if "context" in k.lower()}
            if ctx_keys:
                print(f"  Context-related keys: {json.dumps(ctx_keys, indent=4)}")
            else:
                print(f"  No 'context' keys found. All keys: {list(info[key].keys())}")

    # 3) Check 'parameters' string for num_ctx
    if "parameters" in info:
        params = info["parameters"]
        print(f"\n  --- parameters (raw string) ---")
        print(f"  {repr(params)}")
        for line in str(params).splitlines():
            if "num_ctx" in line.lower() or "context" in line.lower():
                print(f"  >>> FOUND: {line.strip()}")

    # 4) Check 'details' dict
    if "details" in info and isinstance(info["details"], dict):
        print(f"\n  --- details ---")
        print(f"  {json.dumps(info['details'], indent=4, default=str)}")

    # 5) Dump everything for inspection (truncated)
    print(f"\n  --- FULL DUMP (truncated) ---")
    dump = json.dumps(info, indent=2, default=str)
    if len(dump) > 3000:
        print(dump[:3000] + "\n  ... [truncated]")
    else:
        print(dump)


def main():
    client = ollama.Client()

    # List installed models
    try:
        models_response = client.list()
    except Exception as e:
        print(f"Cannot connect to Ollama: {e}")
        print("Make sure Ollama is running (ollama serve)")
        sys.exit(1)

    # Handle both dict and object response formats
    if isinstance(models_response, dict):
        models = models_response.get("models", [])
    else:
        models = getattr(models_response, "models", [])

    if not models:
        print("No models installed. Pull one first: ollama pull llama3.2:3b")
        sys.exit(1)

    names = []
    for m in models:
        name = m.get("name", m.get("model", "???")) if isinstance(m, dict) else getattr(m, "name", getattr(m, "model", "???"))
        names.append(name)

    print(f"Installed models: {names}")

    for name in names:
        probe_model(name)


if __name__ == "__main__":
    main()