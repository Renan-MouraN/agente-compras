"""Schemas da API HTTP. Valores monetários saem como string ("5999.97") para não perder precisão."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

Dinheiro = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str)]


class PedidoIn(BaseModel):
    pedido: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)] = Field(
        examples=["Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada."]
    )


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    produto_id: int
    titulo: str
    categoria: str | None
    marca: str | None
    preco_unitario: Dinheiro
    quantidade: int
    subtotal: Dinheiro
    justificativa: str | None


class RequisicaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    numero: str
    pedido: str
    status: str
    mensagem: str
    pendencias: str | None
    moeda: str
    valor_total: Dinheiro
    modelo_llm: str
    criado_em: datetime
    itens: list[ItemOut]


class ChamadaFerramentaOut(BaseModel):
    ferramenta: str
    argumentos: Any
    sucesso: bool
    erro: str | None = None


class RespostaPedido(BaseModel):
    status: Literal["criada", "nao_atendida"]
    mensagem: str
    pendencias: str | None = None
    requisicao: RequisicaoOut | None = None
    ferramentas: list[ChamadaFerramentaOut] = []


class Erro(BaseModel):
    erro: str
    mensagem: str
