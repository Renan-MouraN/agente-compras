"""Retentativa de inicialização do banco: só erros transitórios são retentados."""

import socket
from contextlib import asynccontextmanager

import pytest
from asyncpg import exceptions as pg
from sqlalchemy import exc as sa

from app.db import criar_engine, criar_tabelas, erro_transitorio


def _embrulhado(original: Exception) -> sa.DBAPIError:
    """Imita o que o SQLAlchemy entrega: DBAPIError com o erro do driver em .orig/__cause__."""
    erro = sa.OperationalError("connect", None, original)
    erro.__cause__ = original
    return erro


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("recusado"),
        socket.gaierror("postgres: nome ainda não resolve"),
        TimeoutError(),
        _embrulhado(pg.CannotConnectNowError("the database system is starting up")),
        _embrulhado(pg.TooManyConnectionsError("too many connections")),
        _embrulhado(pg.ConnectionDoesNotExistError("connection was closed")),
    ],
)
def test_erros_transitorios(exc):
    assert erro_transitorio(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        _embrulhado(pg.InvalidPasswordError("password authentication failed")),
        _embrulhado(pg.InvalidCatalogNameError('database "x" does not exist')),
        ValueError("bug"),
    ],
)
def test_erros_permanentes(exc):
    assert erro_transitorio(exc) is False


class _Conn:
    async def run_sync(self, fn):
        self.executado = True


class EngineFalso:
    def __init__(self, falhas):
        self.falhas = list(falhas)
        self.tentativas = 0

    @asynccontextmanager
    async def begin(self):
        self.tentativas += 1
        if self.falhas:
            raise self.falhas.pop(0)
        yield _Conn()


async def test_recupera_depois_de_falhas_transitorias():
    engine = EngineFalso([
        ConnectionRefusedError(),
        _embrulhado(pg.CannotConnectNowError("the database system is starting up")),
    ])
    await criar_tabelas(engine, tentativas=5, espera=0)
    assert engine.tentativas == 3


async def test_erro_permanente_falha_na_primeira_tentativa():
    engine = EngineFalso([_embrulhado(pg.InvalidPasswordError("password authentication failed"))])
    with pytest.raises(sa.OperationalError):
        await criar_tabelas(engine, tentativas=5, espera=0)
    assert engine.tentativas == 1


async def test_desiste_apos_esgotar_tentativas_com_banco_real_inacessivel():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    engine = criar_engine(f"postgresql+asyncpg://u:p@127.0.0.1:{porta}/db")
    try:
        with pytest.raises(OSError):
            await criar_tabelas(engine, tentativas=2, espera=0)
    finally:
        await engine.dispose()
