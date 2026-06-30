"""
agents/model_client.py
Unified model interface supporting both Ollama (local) and Anthropic (API).

Set MODEL_PROVIDER in your .env file:
    MODEL_PROVIDER=ollama       # Fully local, no API key needed
    MODEL_PROVIDER=anthropic    # Claude via Anthropic API

Defaults to ollama if not set.
"""

import os


MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "ollama").lower()


def chat(prompt: str, max_tokens: int = 1000) -> str:
    """
    Unified chat function. Routes to Ollama or Anthropic based on MODEL_PROVIDER.
    All agents call this instead of the provider SDKs directly.
    """
    if MODEL_PROVIDER == "anthropic":
        return _chat_anthropic(prompt, max_tokens)
    else:
        return _chat_ollama(prompt, max_tokens)


def _chat_ollama(prompt: str, max_tokens: int) -> str:
    import ollama
    model = os.environ.get("OLLAMA_MODEL", "mistral")
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"num_predict": max_tokens}
    )
    return response["message"]["content"]


def _chat_anthropic(prompt: str, max_tokens: int) -> str:
    from anthropic import Anthropic
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def provider_info() -> dict:
    """Return current provider configuration for display in the UI."""
    if MODEL_PROVIDER == "anthropic":
        return {
            "provider": "Anthropic",
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "local": False
        }
    else:
        return {
            "provider": "Ollama",
            "model": os.environ.get("OLLAMA_MODEL", "mistral"),
            "local": True
        }