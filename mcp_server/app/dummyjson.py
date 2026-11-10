"""Cliente HTTP mínimo para a DummyJSON, com erros tipados.

Toda falha de integração vira um `CatalogoErro` com um `codigo` estável, para que
quem chama (as ferramentas MCP e, depois, o backend) consiga distinguir
"o catálogo caiu" de "esse produto não existe".
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class CatalogoErro(Exception):
    """Falha ao consultar o catálogo de produtos."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


# Códigos de erro expostos pelas ferramentas.
CATALOGO_TIMEOUT = "catalogo_timeout"
CATALOGO_INDISPONIVEL = "catalogo_indisponivel"
PRODUTO_NAO_ENCONTRADO = "produto_nao_encontrado"


def _base_url() -> str:
    return os.getenv("DUMMYJSON_BASE_URL", "https://dummyjson.com").rstrip("/")


def _timeout() -> float:
    return float(os.getenv("DUMMYJSON_TIMEOUT_SECONDS", "10"))


async def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET na DummyJSON. Levanta `CatalogoErro` para qualquer falha."""
    timeout = _timeout()
    try:
        async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as client:
            resp = await client.get(path, params=params)
    except httpx.TimeoutException as exc:
        raise CatalogoErro(
            CATALOGO_TIMEOUT,
            f"A API de produtos (DummyJSON) não respondeu em {timeout:g}s. Tente novamente em instantes.",
        ) from exc
    except httpx.HTTPError as exc:
        raise CatalogoErro(
            CATALOGO_INDISPONIVEL,
            "Não foi possível conectar à API de produtos (DummyJSON). Tente novamente em instantes.",
        ) from exc

    if resp.status_code == 404:
        raise CatalogoErro(PRODUTO_NAO_ENCONTRADO, "Produto não encontrado no catálogo.")
    if resp.status_code >= 400:
        raise CatalogoErro(
            CATALOGO_INDISPONIVEL,
            f"A API de produtos (DummyJSON) respondeu com erro HTTP {resp.status_code}. Tente novamente em instantes.",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise CatalogoErro(
            CATALOGO_INDISPONIVEL, "A API de produtos (DummyJSON) devolveu uma resposta inválida."
        ) from exc
    if not isinstance(data, dict):
        raise CatalogoErro(CATALOGO_INDISPONIVEL, "A API de produtos (DummyJSON) devolveu um formato inesperado.")
    return data
