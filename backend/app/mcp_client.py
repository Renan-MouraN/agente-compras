"""Ponte entre o agente e o servidor MCP.

- Descobre as ferramentas via `list_tools` (o servidor MCP é a fonte da verdade dos schemas)
  e as converte para o formato de *function calling* da OpenRouter/OpenAI.
- Executa chamadas e normaliza o resultado em `ResultadoFerramenta`.
- Traduz falhas de transporte em `AppError` com mensagens claras.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
import httpx
import httpx2
from mcp import Client

from app import errors
from app.errors import AppError


@dataclass
class ResultadoFerramenta:
    ok: bool
    texto: str  # conteúdo devolvido ao LLM
    dados: Any = None  # saída estruturada (quando ok)
    erro_codigo: str | None = None
    erro_mensagem: str | None = None


def _ler_erro(texto: str) -> tuple[str, str]:
    """Extrai {"erro","mensagem"} do texto de erro da ferramenta.

    O SDK prefixa a mensagem com "Error executing tool <nome>: ", por isso
    procuramos o JSON a partir da primeira chave.
    """
    try:
        payload = json.loads(texto[texto.index("{") :])
        return str(payload["erro"]), str(payload.get("mensagem", texto))
    except (ValueError, KeyError, TypeError):
        return "erro_ferramenta", texto


class CatalogoMCP:
    def __init__(self, client: Client, timeout: float):
        self._client = client
        self._timeout = timeout

    async def ferramentas_openai(self) -> list[dict[str, Any]]:
        with anyio.fail_after(self._timeout):
            tools = (await self._client.list_tools()).tools
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    async def chamar(self, nome: str, argumentos: dict[str, Any]) -> ResultadoFerramenta:
        try:
            with anyio.fail_after(self._timeout):
                result = await self._client.call_tool(nome, argumentos)
        except TimeoutError as exc:
            raise errors.mcp_timeout() from exc

        texto = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
        if result.is_error:
            codigo, mensagem = _ler_erro(texto)
            return ResultadoFerramenta(ok=False, texto=texto, erro_codigo=codigo, erro_mensagem=mensagem)

        dados = result.structured_content
        if dados is not None:
            texto = json.dumps(dados, ensure_ascii=False)
        return ResultadoFerramenta(ok=True, texto=texto, dados=dados)


# --------------------------------------------------------------------------- conexão

_ERROS_DE_REDE = (httpx.HTTPError, httpx2.HTTPError, OSError)


def _folhas(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _folhas(sub)
    else:
        yield exc


def _traduzir(exc: Exception) -> BaseException:
    """O SDK MCP (anyio) embrulha exceções em ExceptionGroups aninhados.
    Recupera o erro de negócio original ou converte falha de rede em AppError."""
    folhas = list(_folhas(exc))
    for f in folhas:
        if isinstance(f, AppError):
            return f
    if any(isinstance(f, TimeoutError) for f in folhas):
        return errors.mcp_timeout()
    if any(isinstance(f, _ERROS_DE_REDE) for f in folhas):
        return errors.mcp_indisponivel()
    return folhas[0] if len(folhas) == 1 else exc


@asynccontextmanager
async def conectar(url: str, timeout: float) -> AsyncIterator[CatalogoMCP]:
    try:
        async with Client(url, read_timeout_seconds=timeout) as client:
            yield CatalogoMCP(client, timeout)
    except Exception as exc:
        traduzido = _traduzir(exc)
        if traduzido is exc:
            raise
        raise traduzido from exc
