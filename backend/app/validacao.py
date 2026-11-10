"""Validação da proposta do agente antes de gravar.

Princípio: o LLM só escolhe *quais* produtos e *quantas* unidades. Tudo o que é dado de
catálogo (título, preço, estoque, pedido mínimo) vem do que as ferramentas MCP devolveram
nesta mesma execução, e os valores são calculados aqui com Decimal. Assim o agente não
consegue inventar produto nem preço, e o total é sempre reprodutível.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, Field, ValidationError

CENTAVO = Decimal("0.01")
MAX_ITENS = 20


def dinheiro(valor: Any) -> Decimal:
    """Converte para Decimal com 2 casas. `str()` evita herdar o erro binário do float."""
    return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- entrada (vinda do LLM)


class ItemProposto(BaseModel):
    produto_id: int = Field(ge=1)
    quantidade: int = Field(ge=1, le=100_000)
    justificativa: str | None = Field(default=None, max_length=500)


class ConclusaoPedido(BaseModel):
    mensagem: str = Field(min_length=1, max_length=4000)
    itens: list[ItemProposto] = Field(default_factory=list, max_length=MAX_ITENS)
    pendencias: str | None = Field(default=None, max_length=2000)
    preco_unitario_maximo: Decimal | None = Field(default=None, gt=0)
    orcamento_total_maximo: Decimal | None = Field(default=None, gt=0)


# --------------------------------------------------------------------------- saída (validada)


@dataclass(frozen=True)
class ItemValidado:
    produto_id: int
    titulo: str
    categoria: str | None
    marca: str | None
    preco_unitario: Decimal
    quantidade: int
    subtotal: Decimal
    justificativa: str | None


@dataclass(frozen=True)
class PropostaValidada:
    mensagem: str
    pendencias: str | None
    itens: list[ItemValidado]
    valor_total: Decimal


class PropostaInvalida(Exception):
    def __init__(self, problemas: list[str]):
        super().__init__("; ".join(problemas))
        self.problemas = problemas


# --------------------------------------------------------------------------- catálogo observado


def registrar_produtos(catalogo: dict[int, dict[str, Any]], dados: Any) -> None:
    """Guarda os produtos presentes na saída de buscar_produtos/detalhar_produto."""
    if not isinstance(dados, dict):
        return
    produtos = dados.get("produtos") if "produtos" in dados else [dados]
    for p in produtos or []:
        if isinstance(p, dict) and isinstance(p.get("id"), int) and "preco" in p:
            # detalhar_produto pode trazer dados mais completos; mescla sem perder campos.
            catalogo[p["id"]] = {**catalogo.get(p["id"], {}), **p}


def _fmt(valor: Decimal) -> str:
    return f"US$ {valor:,.2f}"


def validar_proposta(args: Any, catalogo: dict[int, dict[str, Any]]) -> PropostaValidada:
    try:
        proposta = ConclusaoPedido.model_validate(args)
    except ValidationError as exc:
        problemas = [f"{'.'.join(str(p) for p in e['loc']) or 'argumentos'}: {e['msg']}" for e in exc.errors()]
        raise PropostaInvalida(problemas) from exc

    problemas: list[str] = []
    itens: list[ItemValidado] = []
    vistos: set[int] = set()

    for item in proposta.itens:
        pid = item.produto_id
        if pid in vistos:
            problemas.append(f"Produto {pid} aparece mais de uma vez; some as quantidades em um único item.")
            continue
        vistos.add(pid)

        produto = catalogo.get(pid)
        if produto is None:
            problemas.append(
                f"Produto {pid} não foi retornado pelas ferramentas nesta conversa. "
                "Use apenas IDs obtidos com buscar_produtos ou detalhar_produto."
            )
            continue

        preco = dinheiro(produto["preco"])
        estoque = int(produto.get("estoque") or 0)
        minimo = int(produto.get("pedido_minimo") or 1)
        titulo = produto.get("titulo") or f"Produto {pid}"

        if preco <= 0:
            problemas.append(f"Produto {pid} ({titulo}) está sem preço válido no catálogo.")
        if item.quantidade > estoque:
            problemas.append(
                f"Produto {pid} ({titulo}): quantidade {item.quantidade} maior que o estoque disponível ({estoque})."
            )
        if item.quantidade < minimo:
            problemas.append(
                f"Produto {pid} ({titulo}): quantidade {item.quantidade} abaixo do pedido mínimo ({minimo})."
            )
        if proposta.preco_unitario_maximo is not None and preco > proposta.preco_unitario_maximo:
            problemas.append(
                f"Produto {pid} ({titulo}) custa {_fmt(preco)}, acima do preço unitário máximo "
                f"de {_fmt(proposta.preco_unitario_maximo)}."
            )

        itens.append(
            ItemValidado(
                produto_id=pid,
                titulo=titulo,
                categoria=produto.get("categoria"),
                marca=produto.get("marca"),
                preco_unitario=preco,
                quantidade=item.quantidade,
                subtotal=(preco * item.quantidade).quantize(CENTAVO),
                justificativa=item.justificativa,
            )
        )

    total = sum((i.subtotal for i in itens), Decimal("0.00"))
    if proposta.orcamento_total_maximo is not None and total > proposta.orcamento_total_maximo:
        problemas.append(
            f"Total de {_fmt(total)} ultrapassa o orçamento máximo de {_fmt(proposta.orcamento_total_maximo)}."
        )

    if problemas:
        raise PropostaInvalida(problemas)

    return PropostaValidada(
        mensagem=proposta.mensagem.strip(),
        pendencias=(proposta.pendencias or "").strip() or None,
        itens=itens,
        valor_total=total,
    )
