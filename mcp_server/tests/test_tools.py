"""Testes das ferramentas do servidor MCP.

Rodam sem chave de LLM e sem internet: o servidor é exercitado em processo por um
cliente MCP real (`mcp.Client`) e a DummyJSON é simulada com `respx`.
Os testes marcados com `live` batem na DummyJSON de verdade (pytest -m live).
"""

import json

import httpx
import pytest
import respx
from mcp import Client

from app.server import mcp

BASE = "https://dummyjson.com"

LAPTOP = {
    "id": 78,
    "title": "Apple MacBook Pro 14 Inch Space Grey",
    "description": "The MacBook Pro 14 Inch in Space Grey is a powerful and sleek laptop.",
    "category": "laptops",
    "price": 1999.99,
    "discountPercentage": 18.52,
    "rating": 3.65,
    "stock": 24,
    "tags": ["laptops", "apple"],
    "brand": "Apple",
    "sku": "LAP-APP-APP-078",
    "warrantyInformation": "3 year warranty",
    "shippingInformation": "Ships in 1 week",
    "availabilityStatus": "In Stock",
    "returnPolicy": "7 days return policy",
    "minimumOrderQuantity": 1,
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DUMMYJSON_BASE_URL", BASE)
    monkeypatch.setenv("DUMMYJSON_TIMEOUT_SECONDS", "2")


def _erro(result) -> dict:
    assert result.is_error is True
    texto = result.content[0].text
    # O SDK prefixa a mensagem com "Error executing tool <nome>: "; o payload é o JSON.
    return json.loads(texto[texto.index("{") :])


# --------------------------------------------------------------------------- descoberta


async def test_lista_as_duas_ferramentas_com_schema():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert set(tools) == {"buscar_produtos", "detalhar_produto"}
    assert tools["buscar_produtos"].input_schema["required"] == ["termo"]
    assert tools["detalhar_produto"].input_schema["required"] == ["id"]
    assert tools["detalhar_produto"].input_schema["properties"]["id"]["type"] == "integer"
    assert tools["buscar_produtos"].output_schema is not None


# --------------------------------------------------------------------------- buscar_produtos


@respx.mock
async def test_buscar_produtos_retorna_produtos_normalizados():
    route = respx.get(f"{BASE}/products/search").mock(
        return_value=httpx.Response(200, json={"products": [LAPTOP], "total": 1, "skip": 0, "limit": 20})
    )

    async with Client(mcp) as client:
        result = await client.call_tool("buscar_produtos", {"termo": " laptop "})

    assert result.is_error is False
    assert route.called
    assert route.calls.last.request.url.params["q"] == "laptop"
    data = result.structured_content
    assert data["termo"] == "laptop"
    assert data["total_encontrado"] == 1
    assert data["produtos"][0] == {
        "id": 78,
        "titulo": "Apple MacBook Pro 14 Inch Space Grey",
        "categoria": "laptops",
        "marca": "Apple",
        "preco": 1999.99,
        "estoque": 24,
        "pedido_minimo": 1,
        "disponibilidade": "In Stock",
        "avaliacao": 3.65,
    }


@respx.mock
async def test_buscar_produtos_sem_resultado_nao_e_erro():
    respx.get(f"{BASE}/products/search").mock(
        return_value=httpx.Response(200, json={"products": [], "total": 0, "skip": 0, "limit": 20})
    )

    async with Client(mcp) as client:
        result = await client.call_tool("buscar_produtos", {"termo": "unicornio"})

    assert result.is_error is False
    assert result.structured_content["produtos"] == []
    assert result.structured_content["total_encontrado"] == 0


@respx.mock
async def test_buscar_produtos_timeout_da_dummyjson():
    respx.get(f"{BASE}/products/search").mock(side_effect=httpx.ReadTimeout("lento"))

    async with Client(mcp) as client:
        result = await client.call_tool("buscar_produtos", {"termo": "laptop"})

    erro = _erro(result)
    assert erro["erro"] == "catalogo_timeout"
    assert "não respondeu em 2s" in erro["mensagem"]


@respx.mock
async def test_buscar_produtos_dummyjson_fora_do_ar():
    respx.get(f"{BASE}/products/search").mock(return_value=httpx.Response(503))

    async with Client(mcp) as client:
        result = await client.call_tool("buscar_produtos", {"termo": "laptop"})

    assert _erro(result)["erro"] == "catalogo_indisponivel"


async def test_buscar_produtos_rejeita_termo_vazio():
    async with Client(mcp) as client:
        result = await client.call_tool("buscar_produtos", {"termo": ""})

    assert result.is_error is True


# --------------------------------------------------------------------------- detalhar_produto


@respx.mock
async def test_detalhar_produto_retorna_detalhes():
    respx.get(f"{BASE}/products/78").mock(return_value=httpx.Response(200, json=LAPTOP))

    async with Client(mcp) as client:
        result = await client.call_tool("detalhar_produto", {"id": 78})

    assert result.is_error is False
    data = result.structured_content
    assert data["id"] == 78
    assert data["preco"] == 1999.99
    assert data["estoque"] == 24
    assert data["pedido_minimo"] == 1
    assert data["garantia"] == "3 year warranty"
    assert data["descricao"].startswith("The MacBook Pro")


@respx.mock
async def test_detalhar_produto_inexistente():
    respx.get(f"{BASE}/products/99999").mock(
        return_value=httpx.Response(404, json={"message": "Product with id '99999' not found"})
    )

    async with Client(mcp) as client:
        result = await client.call_tool("detalhar_produto", {"id": 99999})

    assert _erro(result)["erro"] == "produto_nao_encontrado"


@respx.mock
async def test_detalhar_produto_falha_de_conexao():
    respx.get(f"{BASE}/products/1").mock(side_effect=httpx.ConnectError("sem rede"))

    async with Client(mcp) as client:
        result = await client.call_tool("detalhar_produto", {"id": 1})

    assert _erro(result)["erro"] == "catalogo_indisponivel"


async def test_detalhar_produto_rejeita_id_invalido():
    async with Client(mcp) as client:
        result = await client.call_tool("detalhar_produto", {"id": 0})

    assert result.is_error is True


# --------------------------------------------------------------------------- integração real (opcional)


@pytest.mark.live
async def test_live_buscar_e_detalhar_laptop():
    async with Client(mcp) as client:
        busca = await client.call_tool("buscar_produtos", {"termo": "laptop"})
        assert busca.is_error is False
        primeiro = busca.structured_content["produtos"][0]
        detalhe = await client.call_tool("detalhar_produto", {"id": primeiro["id"]})

    assert detalhe.is_error is False
    assert detalhe.structured_content["preco"] == primeiro["preco"]
