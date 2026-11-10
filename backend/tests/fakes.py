"""Dublês de teste: LLM roteirizado e catálogo MCP em memória."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from app.mcp_client import ResultadoFerramenta

PRODUTOS = {
    78: {"id": 78, "titulo": "Apple MacBook Pro 14 Inch Space Grey", "categoria": "laptops", "marca": "Apple",
         "preco": 1999.99, "estoque": 24, "pedido_minimo": 1, "disponibilidade": "In Stock", "avaliacao": 3.65},
    80: {"id": 80, "titulo": "Huawei Matebook X Pro", "categoria": "laptops", "marca": "Huawei",
         "preco": 1399.99, "estoque": 75, "pedido_minimo": 1, "disponibilidade": "In Stock", "avaliacao": 4.5},
    81: {"id": 81, "titulo": "Lenovo Yoga 920", "categoria": "laptops", "marca": "Lenovo",
         "preco": 1099.99, "estoque": 2, "pedido_minimo": 1, "disponibilidade": "Low Stock", "avaliacao": 2.9},
    5: {"id": 5, "titulo": "Red Nail Polish", "categoria": "beauty", "marca": "Nail Couture",
        "preco": 8.99, "estoque": 79, "pedido_minimo": 5, "disponibilidade": "In Stock", "avaliacao": 3.9},
}

FERRAMENTAS = [
    {"type": "function", "function": {"name": "buscar_produtos", "description": "busca",
                                      "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "detalhar_produto", "description": "detalhe",
                                      "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}}}},
]


def tool_call(nome: str, args: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    return {
        "id": call_id or f"call_{nome}",
        "type": "function",
        "function": {"name": nome, "arguments": json.dumps(args)},
    }


def assistente(*calls: dict[str, Any], content: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = list(calls)
    return msg


class FakeLLM:
    model = "fake/modelo-teste"

    def __init__(self, roteiro: list[dict[str, Any]]):
        self.roteiro = list(roteiro)
        self.chamadas: list[list[dict[str, Any]]] = []

    async def completar(self, messages, tools):
        self.chamadas.append([dict(m) for m in messages])
        self.tools = tools
        if not self.roteiro:
            raise AssertionError("LLM chamado mais vezes do que o roteiro prevê")
        return self.roteiro.pop(0)


class FakeCatalogo:
    """Simula o servidor MCP. `falha` força um erro de ferramenta (ex.: catalogo_timeout)."""

    def __init__(self, falha: str | None = None):
        self.falha = falha
        self.chamadas: list[tuple[str, dict[str, Any]]] = []

    async def ferramentas_openai(self):
        return FERRAMENTAS

    async def chamar(self, nome, argumentos):
        self.chamadas.append((nome, argumentos))
        if self.falha:
            msg = "A API de produtos (DummyJSON) não respondeu em 10s. Tente novamente em instantes."
            return ResultadoFerramenta(ok=False, texto=msg, erro_codigo=self.falha, erro_mensagem=msg)
        if nome == "buscar_produtos":
            termo = argumentos["termo"].lower()
            produtos = [p for p in PRODUTOS.values() if termo in p["categoria"] or termo in p["titulo"].lower()]
            dados = {"termo": termo, "total_encontrado": len(produtos), "produtos": produtos}
            return ResultadoFerramenta(ok=True, texto=json.dumps(dados), dados=dados)
        if nome == "detalhar_produto":
            p = PRODUTOS.get(argumentos["id"])
            if p is None:
                return ResultadoFerramenta(ok=False, texto="não encontrado", erro_codigo="produto_nao_encontrado")
            return ResultadoFerramenta(ok=True, texto=json.dumps(p), dados=p)
        raise AssertionError(nome)

    def conector(self):
        @asynccontextmanager
        async def _ctx():
            yield self

        return _ctx
