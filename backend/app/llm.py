"""Cliente mínimo da OpenRouter (API compatível com OpenAI /chat/completions)."""

from __future__ import annotations

from typing import Any

import httpx

from app import errors
from app.config import Settings
from app.errors import AppError

_MENSAGENS_HTTP = {
    401: "A chave da OpenRouter é inválida ou expirou. Verifique OPENROUTER_API_KEY no .env.",
    402: "A conta da OpenRouter está sem créditos para este modelo.",
    404: "O modelo configurado em OPENROUTER_MODEL não foi encontrado na OpenRouter.",
    429: "A OpenRouter limitou o número de requisições. Aguarde alguns segundos e tente de novo.",
}


class OpenRouterLLM:
    def __init__(self, settings: Settings):
        self.model = settings.openrouter_model
        self._api_key = settings.openrouter_api_key
        self._url = f"{settings.openrouter_base_url}/chat/completions"
        self._timeout = settings.openrouter_timeout_seconds

    async def completar(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Retorna a mensagem do assistente (dict no formato OpenAI)."""
        if not self._api_key:
            raise errors.llm_nao_configurado()

        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Title": "Agente de Compras",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AppError(
                504, "llm_timeout", f"O modelo de linguagem não respondeu em {self._timeout:g}s. Tente novamente."
            ) from exc
        except httpx.HTTPError as exc:
            raise AppError(502, "llm_indisponivel", "Não foi possível conectar à OpenRouter.") from exc

        if resp.status_code >= 400:
            mensagem = _MENSAGENS_HTTP.get(
                resp.status_code, f"A OpenRouter respondeu com erro HTTP {resp.status_code}."
            )
            raise AppError(502, "llm_erro", mensagem)

        try:
            data = resp.json()
        except ValueError as exc:
            raise AppError(502, "llm_erro", "A OpenRouter devolveu uma resposta inválida.") from exc

        # A OpenRouter pode devolver 200 com {"error": {...}} quando o provedor do modelo falha.
        if data.get("error"):
            detalhe = data["error"].get("message", "erro desconhecido") if isinstance(data["error"], dict) else ""
            raise AppError(502, "llm_erro", f"O provedor do modelo falhou: {detalhe}")
        choices = data.get("choices") or []
        if not choices or "message" not in choices[0]:
            raise AppError(502, "llm_erro", "A OpenRouter devolveu uma resposta sem conteúdo.")
        return choices[0]["message"]
