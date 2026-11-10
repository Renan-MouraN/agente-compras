"""Testes da API HTTP com SQLite temporário, LLM roteirizado e catálogo falso."""

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.agent import CONCLUIR
from app.config import Settings
from app.main import create_app
from tests.fakes import FakeCatalogo, FakeLLM, assistente, tool_call

PEDIDO = "Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada."


@pytest.fixture
def settings(tmp_path):
    base = Settings.from_env()
    return dataclasses.replace(
        base, database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", openrouter_api_key="teste", agent_max_steps=5
    )


def _roteiro_feliz():
    return [
        assistente(tool_call("buscar_produtos", {"termo": "laptop"})),
        assistente(tool_call(CONCLUIR, {
            "mensagem": "Separei 3 MacBook Pro.",
            "itens": [{"produto_id": 78, "quantidade": 3, "justificativa": "dentro do limite"}],
            "preco_unitario_maximo": 2000,
        })),
    ]


def _client(settings, roteiro, catalogo=None):
    catalogo = catalogo or FakeCatalogo()
    app = create_app(settings, llm_factory=lambda _: FakeLLM(roteiro), conector_mcp=catalogo.conector())
    return TestClient(app)


def test_post_cria_e_get_lista_requisicao(settings):
    with _client(settings, _roteiro_feliz()) as client:
        resp = client.post("/requisicoes", json={"pedido": PEDIDO})
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["status"] == "criada"
        req = body["requisicao"]
        assert req["numero"] == "REQ-000001"
        assert req["valor_total"] == "5999.97"
        assert req["moeda"] == "USD"
        assert req["itens"][0] == {
            "produto_id": 78,
            "titulo": "Apple MacBook Pro 14 Inch Space Grey",
            "categoria": "laptops",
            "marca": "Apple",
            "preco_unitario": "1999.99",
            "quantidade": 3,
            "subtotal": "5999.97",
            "justificativa": "dentro do limite",
        }
        assert [f["ferramenta"] for f in body["ferramentas"]] == ["buscar_produtos", CONCLUIR]

        lista = client.get("/requisicoes").json()
        assert len(lista) == 1 and lista[0]["id"] == req["id"] and lista[0]["pedido"] == PEDIDO

        assert client.get(f"/requisicoes/{req['id']}").json()["valor_total"] == "5999.97"
        assert client.get("/requisicoes/999").status_code == 404


def test_nada_atende_nao_grava(settings):
    roteiro = [
        assistente(tool_call("buscar_produtos", {"termo": "tablet"})),
        assistente(tool_call(CONCLUIR, {
            "mensagem": "Não encontrei tablets no catálogo.", "itens": [], "pendencias": "Nenhum tablet disponível.",
        })),
    ]
    with _client(settings, roteiro) as client:
        resp = client.post("/requisicoes", json={"pedido": "Preciso de 2 tablets"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "nao_atendida"
        assert resp.json()["requisicao"] is None
        assert resp.json()["pendencias"] == "Nenhum tablet disponível."
        assert client.get("/requisicoes").json() == []


def test_dummyjson_lenta_vira_504_com_mensagem(settings):
    roteiro = [assistente(tool_call("buscar_produtos", {"termo": "laptop"}))]
    with _client(settings, roteiro, FakeCatalogo(falha="catalogo_timeout")) as client:
        resp = client.post("/requisicoes", json={"pedido": PEDIDO})
    assert resp.status_code == 504
    assert resp.json()["erro"] == "catalogo_timeout"
    assert "DummyJSON" in resp.json()["mensagem"]


def test_pedido_vazio_retorna_422_em_portugues(settings):
    with _client(settings, []) as client:
        resp = client.post("/requisicoes", json={"pedido": "  "})
    assert resp.status_code == 422
    assert resp.json()["erro"] == "pedido_invalido"


def test_sem_chave_openrouter_retorna_503(settings):
    sem_chave = dataclasses.replace(settings, openrouter_api_key="")
    app = create_app(sem_chave, conector_mcp=FakeCatalogo().conector())  # LLM real (OpenRouterLLM)
    with TestClient(app) as client:
        resp = client.post("/requisicoes", json={"pedido": PEDIDO})
    assert resp.status_code == 503
    assert resp.json()["erro"] == "llm_nao_configurado"
