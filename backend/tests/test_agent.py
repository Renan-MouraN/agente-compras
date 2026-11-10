import json
from decimal import Decimal

import pytest

from app.agent import CONCLUIR, LEMBRETE, executar_agente
from app.errors import AppError
from tests.fakes import FakeCatalogo, FakeLLM, assistente, tool_call

PEDIDO = "Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada."


def _ultimo_tool_result(llm: FakeLLM) -> dict:
    tool_msgs = [m for m in llm.chamadas[-1] if m["role"] == "tool"]
    return json.loads(tool_msgs[-1]["content"])


async def test_fluxo_completo_cria_requisicao_com_valores_do_catalogo():
    llm = FakeLLM([
        assistente(tool_call("buscar_produtos", {"termo": "laptop"})),
        assistente(tool_call(CONCLUIR, {
            "mensagem": "Escolhi o Huawei Matebook X Pro.",
            "itens": [{"produto_id": 80, "quantidade": 3, "justificativa": "bom custo-benefício"}],
            "preco_unitario_maximo": 2000,
        })),
    ])
    catalogo = FakeCatalogo()

    resultado = await executar_agente(PEDIDO, llm, catalogo, max_steps=5)

    assert resultado.status == "criada"
    assert resultado.proposta.valor_total == Decimal("4199.97")
    assert [c.ferramenta for c in resultado.ferramentas] == ["buscar_produtos", CONCLUIR]
    # O modelo recebe as ferramentas do MCP + concluir_pedido.
    assert {t["function"]["name"] for t in llm.tools} == {"buscar_produtos", "detalhar_produto", CONCLUIR}


async def test_modelo_decide_as_ferramentas_backend_nao_impoe_fluxo():
    # Pedido que não é de compra: o modelo conclui direto, sem nenhuma chamada ao MCP.
    llm = FakeLLM([assistente(tool_call(CONCLUIR, {"mensagem": "Sou um agente de compras.", "itens": []}))])
    catalogo = FakeCatalogo()

    resultado = await executar_agente("Qual a capital da França?", llm, catalogo, max_steps=5)

    assert resultado.status == "nao_atendida"
    assert catalogo.chamadas == []


async def test_produto_inventado_volta_para_o_modelo_corrigir():
    llm = FakeLLM([
        assistente(tool_call("buscar_produtos", {"termo": "laptop"})),
        assistente(tool_call(CONCLUIR, {"mensagem": "x", "itens": [{"produto_id": 4242, "quantidade": 3}]}, "c1")),
        assistente(tool_call(CONCLUIR, {"mensagem": "ok", "itens": [{"produto_id": 78, "quantidade": 3}]}, "c2")),
    ])

    resultado = await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=5)

    assert resultado.status == "criada"
    erro = _ultimo_tool_result(llm)
    assert erro["erro"] == "proposta_invalida"
    assert "4242" in erro["problemas"][0]
    assert [c.sucesso for c in resultado.ferramentas] == [True, False, True]


async def test_sem_consultar_catalogo_nao_grava():
    # Modelo tenta concluir com um ID sem ter buscado nada: rejeitado.
    llm = FakeLLM([
        assistente(tool_call(CONCLUIR, {"mensagem": "x", "itens": [{"produto_id": 78, "quantidade": 1}]})),
        assistente(tool_call(CONCLUIR, {"mensagem": "Não consegui.", "itens": []})),
    ])
    resultado = await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=5)
    assert resultado.status == "nao_atendida"


async def test_busca_e_conclusao_na_mesma_rodada():
    llm = FakeLLM([
        assistente(
            tool_call(CONCLUIR, {"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": 1}]}, "c"),
            tool_call("buscar_produtos", {"termo": "laptop"}, "b"),
        ),
    ])
    resultado = await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=5)
    assert resultado.status == "criada"


@pytest.mark.parametrize(("codigo", "status"), [("catalogo_timeout", 504), ("catalogo_indisponivel", 503)])
async def test_falha_da_dummyjson_interrompe_com_mensagem_clara(codigo, status):
    llm = FakeLLM([assistente(tool_call("buscar_produtos", {"termo": "laptop"}))])

    with pytest.raises(AppError) as exc:
        await executar_agente(PEDIDO, llm, FakeCatalogo(falha=codigo), max_steps=5)

    assert exc.value.codigo == codigo
    assert exc.value.status_code == status
    assert "DummyJSON" in exc.value.mensagem


async def test_texto_livre_recebe_um_lembrete_e_depois_e_aceito():
    llm = FakeLLM([assistente(content="Aqui está..."), assistente(content="Não encontrei nada.")])

    resultado = await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=5)

    assert resultado.status == "nao_atendida"
    assert resultado.mensagem == "Não encontrei nada."
    assert llm.chamadas[1][-1] == {"role": "user", "content": LEMBRETE}


async def test_argumentos_nao_json_voltam_como_erro():
    ruim = {"id": "x", "type": "function", "function": {"name": "buscar_produtos", "arguments": "{termo: laptop"}}
    llm = FakeLLM([assistente(ruim), assistente(tool_call(CONCLUIR, {"mensagem": "nada", "itens": []}))])

    resultado = await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=5)

    assert resultado.ferramentas[0].erro == "argumentos_invalidos"


async def test_limite_de_etapas():
    llm = FakeLLM([assistente(tool_call("buscar_produtos", {"termo": "laptop"}, f"c{i}")) for i in range(3)])

    with pytest.raises(AppError) as exc:
        await executar_agente(PEDIDO, llm, FakeCatalogo(), max_steps=3)

    assert exc.value.codigo == "agente_sem_conclusao"
