"""Contrato backend <-> servidor MCP real.

Sobe o servidor MCP de verdade (subprocesso, transporte streamable HTTP) apontando para uma
DummyJSON simulada local, e exercita o cliente MCP do backend sem dublês:
descoberta de ferramentas, leitura da saída estruturada, códigos de erro, o ciclo do agente
e a API. Não usa chave de LLM nem internet (o LLM é roteirizado).

Precisa da pasta mcp_server/ e de suas dependências (como no venv de desenvolvimento).
Dentro do container do backend a pasta não existe e o módulo é pulado.
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import mcp_client
from app.agent import CONCLUIR, executar_agente
from app.config import Settings
from app.main import create_app
from app.validacao import registrar_produtos
from tests.fakes import FakeLLM, assistente, tool_call

MCP_DIR = Path(__file__).resolve().parents[2] / "mcp_server"

pytestmark = pytest.mark.skipif(
    not (MCP_DIR / "app" / "server.py").exists(),
    reason="mcp_server/ não está disponível neste ambiente (ex.: container do backend)",
)

LAPTOPS = [
    {"id": 78, "title": "Apple MacBook Pro 14 Inch Space Grey", "category": "laptops", "brand": "Apple",
     "price": 1999.99, "stock": 24, "minimumOrderQuantity": 1, "availabilityStatus": "In Stock", "rating": 3.65},
    {"id": 80, "title": "Huawei Matebook X Pro", "category": "laptops", "brand": "Huawei",
     "price": 1399.99, "stock": 75, "minimumOrderQuantity": 1, "availabilityStatus": "In Stock", "rating": 4.98},
]


class _DummyJSONSimulada(BaseHTTPRequestHandler):
    """Rotas mínimas da DummyJSON. O termo 'falha503' simula a API fora do ar."""

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/products/search":
            termo = parse_qs(url.query).get("q", [""])[0]
            if termo == "falha503":
                return self._responder(503, {"message": "Service Unavailable"})
            produtos = [p for p in LAPTOPS if termo in p["category"] or termo in p["title"].lower()]
            return self._responder(200, {"products": produtos, "total": len(produtos), "skip": 0, "limit": 20})
        if url.path.startswith("/products/"):
            pid = url.path.rsplit("/", 1)[-1]
            produto = next((p for p in LAPTOPS if str(p["id"]) == pid), None)
            if produto is None:
                return self._responder(404, {"message": f"Product with id '{pid}' not found"})
            return self._responder(200, {**produto, "description": "desc", "warrantyInformation": "1 year"})
        return self._responder(404, {})

    def _responder(self, status, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *args):  # silencia o log por requisição
        pass


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def dummyjson_url():
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _DummyJSONSimulada)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


@pytest.fixture(scope="module")
def mcp_url(dummyjson_url):
    porta = _porta_livre()
    env = {
        **os.environ,
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(porta),
        "DUMMYJSON_BASE_URL": dummyjson_url,
        "DUMMYJSON_TIMEOUT_SECONDS": "3",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.server"], cwd=MCP_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        prazo = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{porta}/health", timeout=1)
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > prazo:
                    saida = proc.stdout.read().decode() if proc.poll() is not None else "(ainda rodando)"
                    pytest.fail(f"servidor MCP não subiu: {saida[-2000:]}")
                time.sleep(0.2)
        yield f"http://127.0.0.1:{porta}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------- cliente MCP do backend


async def test_descoberta_converte_ferramentas_para_o_formato_da_openrouter(mcp_url):
    async with mcp_client.conectar(mcp_url, timeout=10) as catalogo:
        ferramentas = {t["function"]["name"]: t for t in await catalogo.ferramentas_openai()}

    assert set(ferramentas) == {"buscar_produtos", "detalhar_produto"}
    for t in ferramentas.values():
        assert t["type"] == "function"
        assert t["function"]["description"]
        assert t["function"]["parameters"]["type"] == "object"
    assert ferramentas["buscar_produtos"]["function"]["parameters"]["required"] == ["termo"]
    assert ferramentas["detalhar_produto"]["function"]["parameters"]["properties"]["id"]["type"] == "integer"


async def test_busca_devolve_saida_estruturada_que_alimenta_a_validacao(mcp_url):
    async with mcp_client.conectar(mcp_url, timeout=10) as catalogo:
        resultado = await catalogo.chamar("buscar_produtos", {"termo": "laptop"})

    assert resultado.ok is True
    assert json.loads(resultado.texto) == resultado.dados  # o LLM recebe o mesmo JSON estruturado
    vistos: dict = {}
    registrar_produtos(vistos, resultado.dados)
    assert vistos[80]["preco"] == 1399.99
    assert vistos[80]["estoque"] == 75
    assert vistos[80]["pedido_minimo"] == 1


async def test_detalhe_inexistente_vira_codigo_produto_nao_encontrado(mcp_url):
    async with mcp_client.conectar(mcp_url, timeout=10) as catalogo:
        resultado = await catalogo.chamar("detalhar_produto", {"id": 4242})

    assert resultado.ok is False
    assert resultado.erro_codigo == "produto_nao_encontrado"


async def test_dummyjson_fora_do_ar_vira_codigo_catalogo_indisponivel(mcp_url):
    async with mcp_client.conectar(mcp_url, timeout=10) as catalogo:
        resultado = await catalogo.chamar("buscar_produtos", {"termo": "falha503"})

    assert resultado.ok is False
    assert resultado.erro_codigo == "catalogo_indisponivel"
    assert "DummyJSON" in resultado.erro_mensagem


async def test_agente_sobre_o_mcp_real_cria_requisicao_com_preco_do_catalogo(mcp_url):
    llm = FakeLLM([
        assistente(tool_call("buscar_produtos", {"termo": "laptop"})),
        assistente(tool_call("detalhar_produto", {"id": 80})),
        assistente(tool_call(CONCLUIR, {"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": 3}]})),
    ])
    async with mcp_client.conectar(mcp_url, timeout=10) as catalogo:
        resultado = await executar_agente("3 laptops até US$ 2.000", llm, catalogo, max_steps=5)

    assert resultado.status == "criada"
    assert resultado.proposta.valor_total == Decimal("4199.97")
    assert [c.ferramenta for c in resultado.ferramentas] == ["buscar_produtos", "detalhar_produto", CONCLUIR]


# --------------------------------------------------------------------------- API ponta a ponta


@pytest.fixture
def settings(tmp_path, mcp_url):
    return dataclasses.replace(
        Settings.from_env(),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'contrato.db'}",
        mcp_server_url=mcp_url,
        mcp_timeout_seconds=10,
        openrouter_api_key="teste",
        agent_max_steps=5,
        agent_timeout_seconds=30,
    )


def test_api_com_mcp_real_grava_requisicao(settings):
    roteiro = [
        assistente(tool_call("buscar_produtos", {"termo": "laptop"})),
        assistente(tool_call(CONCLUIR, {"mensagem": "ok", "itens": [{"produto_id": 78, "quantidade": 3}]})),
    ]
    with TestClient(create_app(settings, llm_factory=lambda _: FakeLLM(roteiro))) as client:
        resp = client.post("/requisicoes", json={"pedido": "Preciso de 3 laptops"})
        assert resp.status_code == 201, resp.text
        assert resp.json()["requisicao"]["valor_total"] == "5999.97"
        assert len(client.get("/requisicoes").json()) == 1


def test_api_com_mcp_real_dummyjson_fora_do_ar_retorna_503_sem_gravar(settings):
    # O erro atravessa o ExceptionGroup real do SDK MCP e chega ao chat como mensagem clara.
    roteiro = [assistente(tool_call("buscar_produtos", {"termo": "falha503"}))]
    with TestClient(create_app(settings, llm_factory=lambda _: FakeLLM(roteiro))) as client:
        resp = client.post("/requisicoes", json={"pedido": "Preciso de 3 laptops"})
        assert resp.status_code == 503
        assert resp.json()["erro"] == "catalogo_indisponivel"
        assert client.get("/requisicoes").json() == []


def test_api_com_mcp_inacessivel_retorna_503(settings):
    sem_mcp = dataclasses.replace(settings, mcp_server_url=f"http://127.0.0.1:{_porta_livre()}/mcp")
    with TestClient(create_app(sem_mcp, llm_factory=lambda _: FakeLLM([]))) as client:
        resp = client.post("/requisicoes", json={"pedido": "Preciso de 3 laptops"})
    assert resp.status_code == 503
    assert resp.json()["erro"] == "mcp_indisponivel"
