"""Cliente mínimo da OpenRouter (API compatível com OpenAI /chat/completions)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app import errors
from app.config import Settings
from app.errors import AppError

log = logging.getLogger(__name__)

# Falhas de conexão em que vale tentar de novo: DNS, recusa, conexão caída antes da resposta.
# Timeouts NÃO entram (cada tentativa pode levar até OPENROUTER_TIMEOUT_SECONDS e estouraria o
# prazo do agente) e erros HTTP (401, 402, 429...) também não, pois não se resolvem repetindo.
_ERROS_DE_CONEXAO = (httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)

_MENSAGENS_HTTP = {
    401: "A chave da OpenRouter é inválida ou expirou. Verifique OPENROUTER_API_KEY no .env.",
    402: "A conta da OpenRouter está sem créditos para este modelo.",
    404: "O modelo configurado em OPENROUTER_MODEL não foi encontrado na OpenRouter.",
    429: "A OpenRouter limitou o número de requisições. Aguarde alguns segundos e tente de novo.",
}


class OpenRouterLLM:
    def __init__(self, settings: Settings, *, tentativas: int = 3, espera: float = 1.0):
        self.model = settings.openrouter_model
        self._api_key = settings.openrouter_api_key
        self._url = f"{settings.openrouter_base_url}/chat/completions"
        self._timeout = settings.openrouter_timeout_seconds
        self._tentativas = tentativas
        self._espera = espera  # 1s, 2s, ... entre as tentativas

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
        resp = await self._post(body, headers)

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

    async def _post(self, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        for tentativa in range(1, self._tentativas + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    return await client.post(self._url, json=body, headers=headers)
            except httpx.TimeoutException as exc:
                raise AppError(
                    504, "llm_timeout", f"O modelo de linguagem não respondeu em {self._timeout:g}s. Tente novamente."
                ) from exc
            except _ERROS_DE_CONEXAO as exc:
                causa = f"{type(exc).__name__}: {exc}"
                if tentativa == self._tentativas:
                    log.error("OpenRouter inacessível após %d tentativas (%s)", self._tentativas, causa)
                    raise AppError(
                        502,
                        "llm_indisponivel",
                        f"Não foi possível conectar à OpenRouter após {self._tentativas} tentativas. "
                        "Verifique a conexão com a internet e tente novamente.",
                    ) from exc
                espera = self._espera * tentativa
                log.warning(
                    "falha de conexão com a OpenRouter (tentativa %d/%d: %s); nova tentativa em %.0fs",
                    tentativa, self._tentativas, causa, espera,
                )
                await asyncio.sleep(espera)
            except httpx.HTTPError as exc:
                log.error("erro inesperado ao chamar a OpenRouter (%s: %s)", type(exc).__name__, exc)
                raise AppError(502, "llm_indisponivel", "Não foi possível conectar à OpenRouter.") from exc
        raise AssertionError("inalcançável")  # o laço sempre retorna ou levanta
