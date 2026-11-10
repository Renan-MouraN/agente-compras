"""Engine/sessões assíncronas e criação automática das tabelas no startup."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.models import Base

log = logging.getLogger(__name__)


def criar_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def criar_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def criar_tabelas(engine: AsyncEngine, tentativas: int = 10, espera: float = 2.0) -> None:
    """CREATE TABLE IF NOT EXISTS para todos os modelos.

    Tenta algumas vezes porque, mesmo com o healthcheck do compose, o Postgres pode
    recusar conexões por alguns instantes logo após subir.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            log.info("tabelas verificadas/criadas")
            return
        except (OSError, ConnectionError) as exc:  # asyncpg levanta subclasses de OSError
            if tentativa == tentativas:
                raise
            log.warning("banco indisponível (%s); nova tentativa em %.0fs", exc, espera)
            await asyncio.sleep(espera)
