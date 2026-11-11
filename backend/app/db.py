"""Engine/sessões assíncronas e criação automática das tabelas no startup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.models import Base

log = logging.getLogger(__name__)

# SQLSTATEs que indicam indisponibilidade passageira do Postgres.
_SQLSTATE_TRANSITORIOS = {
    "57P03",  # cannot_connect_now: "the database system is starting up"
    "53300",  # too_many_connections
}


def criar_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def criar_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _cadeia(exc: BaseException) -> Iterator[BaseException]:
    """A exceção e suas causas (__cause__, __context__ e .orig do SQLAlchemy)."""
    vistos: set[int] = set()
    pilha = [exc]
    while pilha:
        atual = pilha.pop()
        if atual is None or id(atual) in vistos:
            continue
        vistos.add(id(atual))
        yield atual
        pilha.extend([atual.__cause__, atual.__context__, getattr(atual, "orig", None)])


def erro_transitorio(exc: BaseException) -> bool:
    """True para falhas de conexão que tendem a se resolver sozinhas (banco subindo,
    rede/DNS do compose ainda não pronta). Credenciais erradas ou banco inexistente
    são permanentes: falham na hora em vez de ficar tentando."""
    for e in _cadeia(exc):
        if isinstance(e, (OSError, TimeoutError)):  # recusa de conexão, DNS, rede, timeout
            return True
        sqlstate = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
        if isinstance(sqlstate, str) and (sqlstate in _SQLSTATE_TRANSITORIOS or sqlstate.startswith("08")):
            return True
        if type(e).__name__ in {"CannotConnectNowError", "ConnectionDoesNotExistError", "PostgresConnectionError"}:
            return True
    return False


async def criar_tabelas(engine: AsyncEngine, tentativas: int = 15, espera: float = 2.0) -> None:
    """CREATE TABLE IF NOT EXISTS para todos os modelos.

    Tenta de novo em erros transitórios: mesmo com o healthcheck do compose, o Postgres
    pode recusar conexões por alguns instantes (ex.: na primeira inicialização do volume).
    """
    for tentativa in range(1, tentativas + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            log.info("tabelas verificadas/criadas")
            return
        except Exception as exc:
            if not erro_transitorio(exc):
                log.error("erro permanente ao conectar no banco (verifique DATABASE_URL e credenciais): %s", exc)
                raise
            if tentativa == tentativas:
                log.error("banco indisponível após %d tentativas: %s", tentativas, exc)
                raise
            log.warning(
                "banco indisponível (tentativa %d/%d: %s); nova tentativa em %.0fs",
                tentativa, tentativas, type(exc).__name__, espera,
            )
            await asyncio.sleep(espera)
