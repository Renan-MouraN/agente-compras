"""Tabelas: requisicoes (cabeçalho) e itens_requisicao (linhas)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Requisicao(Base):
    __tablename__ = "requisicoes"
    __table_args__ = (CheckConstraint("valor_total >= 0", name="ck_requisicao_total_nao_negativo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pedido: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="criada")
    mensagem: Mapped[str] = mapped_column(Text, nullable=False)
    pendencias: Mapped[str | None] = mapped_column(Text)
    moeda: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    valor_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    modelo_llm: Mapped[str] = mapped_column(String(200), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    itens: Mapped[list[ItemRequisicao]] = relationship(
        back_populates="requisicao", cascade="all, delete-orphan", order_by="ItemRequisicao.id", lazy="selectin"
    )

    @property
    def numero(self) -> str:
        return f"REQ-{self.id:06d}"


class ItemRequisicao(Base):
    __tablename__ = "itens_requisicao"
    __table_args__ = (
        CheckConstraint("quantidade > 0", name="ck_item_quantidade_positiva"),
        CheckConstraint("preco_unitario >= 0", name="ck_item_preco_nao_negativo"),
        UniqueConstraint("requisicao_id", "produto_id", name="uq_item_produto_por_requisicao"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requisicao_id: Mapped[int] = mapped_column(
        ForeignKey("requisicoes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    produto_id: Mapped[int] = mapped_column(nullable=False)
    titulo: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[str | None] = mapped_column(String(100))
    marca: Mapped[str | None] = mapped_column(String(100))
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantidade: Mapped[int] = mapped_column(nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    justificativa: Mapped[str | None] = mapped_column(Text)

    requisicao: Mapped[Requisicao] = relationship(back_populates="itens")
