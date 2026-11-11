"""Tratamento de erros das integrações externas: OpenRouter e servidor MCP."""

import dataclasses
import socket

import httpx
import pytest
import respx

from app import mcp_client
from app.config import Settings
from app.errors import AppError
from app.llm import OpenRouterLLM

URL = "https://openrouter.test/api/v1/chat/completions"


@pytest.fixture
def llm():
    s = dataclasses.replace(
        Settings.from_env(), openrouter_api_key="k", openrouter_base_url="https://openrouter.test/api/v1",
        openrouter_timeout_seconds=1,
    )
    return OpenRouterLLM(s)


@respx.mock
async def test_llm_devolve_mensagem_do_assistente(llm):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "oi"}}]
    }))
    msg = await llm.completar([{"role": "user", "content": "x"}], [])
    assert msg["content"] == "oi"
    assert route.calls.last.request.headers["Authorization"] == "Bearer k"


@respx.mock
@pytest.mark.parametrize(("status", "trecho"), [(401, "inválida ou expirou"), (402, "sem créditos"), (429, "limitou")])
async def test_llm_erros_http_viram_mensagens_claras(llm, status, trecho):
    respx.post(URL).mock(return_value=httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(AppError) as exc:
        await llm.completar([], [])
    assert exc.value.status_code == 502
    assert trecho in exc.value.mensagem


@respx.mock
async def test_llm_timeout(llm):
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("lento"))
    with pytest.raises(AppError) as exc:
        await llm.completar([], [])
    assert exc.value.codigo == "llm_timeout" and exc.value.status_code == 504


@respx.mock
async def test_llm_erro_no_corpo_com_status_200(llm):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"error": {"message": "provider down"}}))
    with pytest.raises(AppError) as exc:
        await llm.completar([], [])
    assert "provider down" in exc.value.mensagem


@pytest.fixture
def llm_sem_espera():
    return OpenRouterLLM(
        dataclasses.replace(
            Settings.from_env(), openrouter_api_key="k", openrouter_base_url="https://openrouter.test/api/v1",
            openrouter_timeout_seconds=1,
        ),
        espera=0,
    )


@respx.mock
async def test_llm_tenta_de_novo_apos_falha_de_conexao_passageira(llm_sem_espera):
    ok = httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "oi"}}]})
    route = respx.post(URL).mock(side_effect=[
        httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        ok,
    ])
    msg = await llm_sem_espera.completar([], [])
    assert msg["content"] == "oi"
    assert route.call_count == 3


@respx.mock
async def test_llm_desiste_apos_tres_falhas_de_conexao_e_registra_a_causa(llm_sem_espera, caplog):
    route = respx.post(URL).mock(side_effect=httpx.ConnectError("[Errno 111] Connection refused"))
    with pytest.raises(AppError) as exc:
        await llm_sem_espera.completar([], [])
    assert exc.value.codigo == "llm_indisponivel" and exc.value.status_code == 502
    assert "após 3 tentativas" in exc.value.mensagem
    assert route.call_count == 3
    assert "ConnectError: [Errno 111] Connection refused" in caplog.text


@respx.mock
@pytest.mark.parametrize(
    "falha",
    [httpx.ReadTimeout("lento"), httpx.Response(429, json={}), httpx.Response(401, json={})],
    ids=["timeout", "429", "401"],
)
async def test_llm_nao_repete_timeout_nem_erro_http(llm_sem_espera, falha):
    if isinstance(falha, Exception):
        route = respx.post(URL).mock(side_effect=falha)
    else:
        route = respx.post(URL).mock(return_value=falha)
    with pytest.raises(AppError):
        await llm_sem_espera.completar([], [])
    assert route.call_count == 1


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_mcp_fora_do_ar_vira_mcp_indisponivel():
    with pytest.raises(AppError) as exc:
        async with mcp_client.conectar(f"http://127.0.0.1:{_porta_livre()}/mcp", timeout=3):
            pass
    assert exc.value.codigo == "mcp_indisponivel"
    assert exc.value.status_code == 503


def test_traduz_appError_embrulhado_em_exception_group():
    original = AppError(504, "catalogo_timeout", "lento")
    grupo = ExceptionGroup("g", [ExceptionGroup("g2", [original])])
    assert mcp_client._traduzir(grupo) is original


def test_le_erro_estruturado_da_ferramenta():
    texto = 'Error executing tool buscar_produtos: {"erro": "catalogo_timeout", "mensagem": "lento"}'
    assert mcp_client._ler_erro(texto) == ("catalogo_timeout", "lento")
    assert mcp_client._ler_erro("Error executing tool x")[0] == "erro_ferramenta"
