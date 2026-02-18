import anthropic
from flask import current_app


def get_client():
    api_key = current_app.config["ANTHROPIC_API_KEY"]
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file."
        )
    return anthropic.Anthropic(api_key=api_key)


def ask(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Send a prompt to Claude and return the text response."""
    client = get_client()
    messages = [{"role": "user", "content": prompt}]
    kwargs = dict(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=messages,
    )
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    return response.content[0].text
