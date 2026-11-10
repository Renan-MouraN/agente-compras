"""Servidor MCP do agente de compras.

Expõe duas ferramentas somente-leitura sobre o catálogo da DummyJSON:

- buscar_produtos(termo)   -> GET /products/search?q=
- detalhar_produto(id)     -> GET /products/{id}

As ferramentas devolvem saída estruturada (schema publicado no list_tools) e,
em caso de falha, um erro de ferramenta (`is_error=True`) cujo texto é um JSON
`{"erro": <codigo>, "mensagem": <texto para humanos>}`.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import dummyjson
from app.dummyjson import CatalogoErro

LIMITE_BUSCA = 20

mcp = MCPServer(
    name="catalogo-produtos",
    instructions=(
        "Catálogo de produtos da DummyJSON. O catálogo está em inglês e os preços estão em dólares (USD). "
        "Use buscar_produtos para encontrar candidatos e detalhar_produto para ver detalhes de um item."
    ),
)


# --------------------------------------------------------------------------- modelos de saída


class ProdutoResumo(BaseModel):
    id: int
    titulo: str
    categoria: str
    marca: str | None = None
    preco: float = Field(description="Preço unitário de catálogo, em USD.")
    estoque: int = Field(description="Unidades disponíveis em estoque.")
    pedido_minimo: int = Field(description="Quantidade mínima por pedido (minimumOrderQuantity).")
    disponibilidade: str | None = None
    avaliacao: float | None = None


class ResultadoBusca(BaseModel):
    termo: str
    total_encontrado: int = Field(description="Total de produtos que casam com o termo no catálogo.")
    produtos: list[ProdutoResumo]


class ProdutoDetalhe(ProdutoResumo):
    descricao: str | None = None
    sku: str | None = None
    garantia: str | None = None
    envio: str | None = None
    politica_devolucao: str | None = None
    tags: list[str] = []


def _resumo(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"],
        "titulo": p.get("title", ""),
        "categoria": p.get("category", ""),
        "marca": p.get("brand"),
        "preco": p.get("price", 0),
        "estoque": p.get("stock", 0),
        "pedido_minimo": p.get("minimumOrderQuantity") or 1,
        "disponibilidade": p.get("availabilityStatus"),
        "avaliacao": p.get("rating"),
    }


def _erro(exc: CatalogoErro) -> ToolError:
    return ToolError(json.dumps({"erro": exc.codigo, "mensagem": exc.mensagem}, ensure_ascii=False))


# --------------------------------------------------------------------------- ferramentas

_SOMENTE_LEITURA = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)


@mcp.tool(annotations=_SOMENTE_LEITURA)
async def buscar_produtos(
    termo: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Palavra-chave em INGLÊS, de preferência no singular (ex.: 'laptop', 'phone', 'mouse').",
        ),
    ],
) -> ResultadoBusca:
    """Busca produtos no catálogo por palavra-chave (título, descrição, marca, categoria).

    O catálogo está em inglês: traduza o pedido antes de buscar (ex.: 'notebook' -> 'laptop',
    'celular' -> 'smartphone'). Retorna até 20 produtos com id, preço em USD, estoque e
    quantidade mínima por pedido. Lista vazia significa que nada no catálogo casa com o termo;
    tente sinônimos antes de concluir que o produto não existe.
    """
    termo = termo.strip()
    try:
        data = await dummyjson.get_json("/products/search", params={"q": termo, "limit": LIMITE_BUSCA})
    except CatalogoErro as exc:
        raise _erro(exc) from exc
    produtos = [ProdutoResumo(**_resumo(p)) for p in data.get("products", [])]
    return ResultadoBusca(termo=termo, total_encontrado=data.get("total", len(produtos)), produtos=produtos)


@mcp.tool(annotations=_SOMENTE_LEITURA)
async def detalhar_produto(
    id: Annotated[int, Field(ge=1, description="ID numérico do produto, obtido em buscar_produtos.")],
) -> ProdutoDetalhe:
    """Retorna os detalhes de um produto do catálogo pelo ID: descrição, preço em USD, estoque,
    quantidade mínima por pedido, garantia e envio. Use para confirmar um item antes de incluí-lo
    na requisição. Se o ID não existir, retorna o erro 'produto_nao_encontrado'.
    """
    try:
        p = await dummyjson.get_json(f"/products/{id}")
    except CatalogoErro as exc:
        raise _erro(exc) from exc
    return ProdutoDetalhe(
        **_resumo(p),
        descricao=p.get("description"),
        sku=p.get("sku"),
        garantia=p.get("warrantyInformation"),
        envio=p.get("shippingInformation"),
        politica_devolucao=p.get("returnPolicy"),
        tags=p.get("tags") or [],
    )


# --------------------------------------------------------------------------- HTTP


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def main() -> None:
    allowed_hosts = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",") if h.strip()]
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8001")),
        # Sem estado de sessão no servidor: cada requisição é independente,
        # o que simplifica escalar/reiniciar o container.
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[f"http://{h}" for h in allowed_hosts],
        ),
    )


if __name__ == "__main__":
    main()
