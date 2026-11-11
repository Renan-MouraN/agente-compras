"""API HTTP do agente de compras.

POST /requisicoes  -> roda o agente e, se houver itens válidos, grava a requisição
GET  /requisicoes  -> lista as requisições salvas (mais recentes primeiro)
GET  /requisicoes/{id}
GET  /health
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import errors, mcp_client
from app.agent import LLM, Catalogo, executar_agente
from app.config import Settings
from app.db import criar_engine, criar_sessionmaker, criar_tabelas
from app.errors import AppError
from app.llm import OpenRouterLLM
from app.models import ItemRequisicao, Requisicao
from app.schemas import ChamadaFerramentaOut, Erro, PedidoIn, RequisicaoOut, RespostaPedido

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("agente-compras")

ConectorMCP = Callable[[], AbstractAsyncContextManager[Catalogo]]

ERROS_DOCUMENTADOS = {
    422: {"model": Erro, "description": "Pedido inválido"},
    502: {"model": Erro, "description": "Falha no LLM / agente não concluiu"},
    503: {"model": Erro, "description": "Catálogo, MCP, LLM ou banco indisponível"},
    504: {"model": Erro, "description": "Timeout do catálogo, MCP, LLM ou prazo total do agente"},
}


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def create_app(
    settings: Settings | None = None,
    *,
    llm_factory: Callable[[Settings], LLM] | None = None,
    conector_mcp: ConectorMCP | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    llm_factory = llm_factory or OpenRouterLLM
    conector_mcp = conector_mcp or (
        lambda: mcp_client.conectar(settings.mcp_server_url, settings.mcp_timeout_seconds)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = criar_engine(settings.database_url)
        await criar_tabelas(engine)
        app.state.sessionmaker = criar_sessionmaker(engine)
        if not settings.openrouter_api_key:
            log.warning("OPENROUTER_API_KEY não definida: o chat responderá com erro até ela ser configurada.")
        log.info("modelo: %s | MCP: %s", settings.openrouter_model, settings.mcp_server_url)
        yield
        await engine.dispose()

    app = FastAPI(
        title="Agente de Compras",
        description="Recebe pedidos em linguagem natural e gera requisições de compra a partir do catálogo DummyJSON.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ erros

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        log.warning("erro %s: %s", exc.codigo, exc.mensagem)
        return _erro(exc.status_code, exc.codigo, exc.mensagem)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return _erro(422, "pedido_invalido", "Descreva o que você precisa comprar (entre 3 e 2000 caracteres).")

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        log.exception("erro de banco", exc_info=exc)
        return _erro(503, "banco_indisponivel", "Não foi possível acessar o banco de dados. Tente novamente.")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("erro inesperado", exc_info=exc)
        return _erro(500, "erro_interno", "Ocorreu um erro inesperado ao processar o pedido.")

    # ------------------------------------------------------------------ rotas

    @app.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok"}

    @app.post(
        "/requisicoes",
        response_model=RespostaPedido,
        status_code=201,
        responses={200: {"model": RespostaPedido, "description": "Nada atendeu; nenhuma requisição gravada"}}
        | ERROS_DOCUMENTADOS,
        tags=["requisicoes"],
    )
    async def criar_requisicao(body: PedidoIn, request: Request, response: Response):
        llm = llm_factory(settings)
        # Prazo global do agente. A gravação fica FORA deste bloco: ou o agente terminou
        # a tempo e a requisição é gravada, ou estourou o prazo e nada é gravado.
        prazo = asyncio.timeout(settings.agent_timeout_seconds)
        try:
            async with prazo:
                async with conector_mcp() as catalogo:
                    resultado = await executar_agente(body.pedido, llm, catalogo, settings.agent_max_steps)
        except TimeoutError as exc:
            if not prazo.expired():
                raise
            raise errors.agente_timeout(settings.agent_timeout_seconds) from exc

        ferramentas = [ChamadaFerramentaOut(**vars(c)) for c in resultado.ferramentas]
        proposta = resultado.proposta

        if resultado.status != "criada" or proposta is None:
            response.status_code = 200
            return RespostaPedido(
                status="nao_atendida",
                mensagem=resultado.mensagem,
                pendencias=proposta.pendencias if proposta else None,
                ferramentas=ferramentas,
            )

        requisicao = Requisicao(
            pedido=body.pedido,
            status="criada",
            mensagem=proposta.mensagem,
            pendencias=proposta.pendencias,
            moeda="USD",
            valor_total=proposta.valor_total,
            modelo_llm=llm.model,
            itens=[
                ItemRequisicao(
                    produto_id=i.produto_id,
                    titulo=i.titulo,
                    categoria=i.categoria,
                    marca=i.marca,
                    preco_unitario=i.preco_unitario,
                    quantidade=i.quantidade,
                    subtotal=i.subtotal,
                    justificativa=i.justificativa,
                )
                for i in proposta.itens
            ],
        )
        async with request.app.state.sessionmaker() as session:
            session.add(requisicao)
            await session.commit()
            await session.refresh(requisicao, attribute_names=["criado_em", "itens"])

        log.info("requisição %s gravada: total %s", requisicao.numero, requisicao.valor_total)
        return RespostaPedido(
            status="criada",
            mensagem=proposta.mensagem,
            pendencias=proposta.pendencias,
            requisicao=RequisicaoOut.model_validate(requisicao),
            ferramentas=ferramentas,
        )

    @app.get("/requisicoes", response_model=list[RequisicaoOut], tags=["requisicoes"])
    async def listar_requisicoes(request: Request, limite: Annotated[int, Query(ge=1, le=200)] = 50):
        async with request.app.state.sessionmaker() as session:
            rows = await session.scalars(select(Requisicao).order_by(Requisicao.id.desc()).limit(limite))
            return [RequisicaoOut.model_validate(r) for r in rows]

    @app.get(
        "/requisicoes/{requisicao_id}",
        response_model=RequisicaoOut,
        responses={404: {"model": Erro}},
        tags=["requisicoes"],
    )
    async def obter_requisicao(request: Request, requisicao_id: Annotated[int, Path(ge=1)]):
        async with request.app.state.sessionmaker() as session:
            requisicao = await session.get(Requisicao, requisicao_id)
            if requisicao is None:
                return _erro(404, "requisicao_nao_encontrada", f"Requisição {requisicao_id} não encontrada.")
            return RequisicaoOut.model_validate(requisicao)

    return app


app = create_app()
