"""Клиент Groq (OpenAI-совместимый Chat Completions) на стандартной библиотеке."""

import json
import urllib.error
import urllib.request

from django.conf import settings

URL = "https://api.groq.com/openai/v1/chat/completions"


class AssistantError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status  # HTTP-код Groq, если он ответил ошибкой


def chat(messages, temperature=0.3, max_tokens=1500):
    if not settings.GROQ_API_KEY:
        raise AssistantError("GROQ_API_KEY не задан (см. файл .env).")
    body = json.dumps(
        {"model": settings.GROQ_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    ).encode()
    request = urllib.request.Request(
        URL,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
            # без своего User-Agent Cloudflare перед Groq может отвечать 403
            "User-Agent": "pharmacy-api/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise AssistantError(f"Groq {e.code}: {detail}", status=e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AssistantError(f"Groq недоступен: {e}") from e
    return (data["choices"][0]["message"].get("content") or "").strip()
