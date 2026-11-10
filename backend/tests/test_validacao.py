from decimal import Decimal

import pytest

from app.validacao import PropostaInvalida, registrar_produtos, validar_proposta
from tests.fakes import PRODUTOS


@pytest.fixture
def catalogo():
    cat: dict = {}
    registrar_produtos(cat, {"termo": "x", "total_encontrado": 4, "produtos": list(PRODUTOS.values())})
    return cat


def test_calcula_total_com_preco_do_catalogo(catalogo):
    proposta = validar_proposta(
        {"mensagem": "ok", "itens": [{"produto_id": 78, "quantidade": 3}], "preco_unitario_maximo": 2000},
        catalogo,
    )
    item = proposta.itens[0]
    assert item.preco_unitario == Decimal("1999.99")
    assert item.subtotal == Decimal("5999.97")
    assert proposta.valor_total == Decimal("5999.97")
    assert item.titulo == "Apple MacBook Pro 14 Inch Space Grey"


def test_soma_sem_erro_de_ponto_flutuante(catalogo):
    # Em float, 1999.99 * 3 + 8.99 * 7 = 6062.900000000001
    proposta = validar_proposta(
        {"mensagem": "ok", "itens": [{"produto_id": 78, "quantidade": 3}, {"produto_id": 5, "quantidade": 7}]},
        catalogo,
    )
    assert proposta.valor_total == Decimal("6062.90")


def test_ignora_preco_informado_pelo_llm(catalogo):
    proposta = validar_proposta(
        {"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": 1, "preco": 1.0}]}, catalogo
    )
    assert proposta.valor_total == Decimal("1399.99")


def test_rejeita_produto_que_nao_veio_das_ferramentas(catalogo):
    with pytest.raises(PropostaInvalida) as exc:
        validar_proposta({"mensagem": "ok", "itens": [{"produto_id": 9999, "quantidade": 1}]}, catalogo)
    assert "não foi retornado pelas ferramentas" in exc.value.problemas[0]


def test_rejeita_quantidade_acima_do_estoque(catalogo):
    with pytest.raises(PropostaInvalida) as exc:
        validar_proposta({"mensagem": "ok", "itens": [{"produto_id": 81, "quantidade": 3}]}, catalogo)
    assert "estoque disponível (2)" in exc.value.problemas[0]


def test_rejeita_quantidade_abaixo_do_pedido_minimo(catalogo):
    with pytest.raises(PropostaInvalida) as exc:
        validar_proposta({"mensagem": "ok", "itens": [{"produto_id": 5, "quantidade": 2}]}, catalogo)
    assert "pedido mínimo (5)" in exc.value.problemas[0]


def test_rejeita_preco_acima_do_limite_unitario(catalogo):
    with pytest.raises(PropostaInvalida) as exc:
        validar_proposta(
            {"mensagem": "ok", "itens": [{"produto_id": 78, "quantidade": 1}], "preco_unitario_maximo": 1500},
            catalogo,
        )
    assert "acima do preço unitário máximo" in exc.value.problemas[0]


def test_rejeita_total_acima_do_orcamento(catalogo):
    with pytest.raises(PropostaInvalida) as exc:
        validar_proposta(
            {"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": 3}], "orcamento_total_maximo": 4000},
            catalogo,
        )
    assert "ultrapassa o orçamento máximo" in exc.value.problemas[0]


def test_rejeita_produto_duplicado(catalogo):
    with pytest.raises(PropostaInvalida):
        validar_proposta(
            {"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": 1}, {"produto_id": 80, "quantidade": 2}]},
            catalogo,
        )


@pytest.mark.parametrize("quantidade", [0, -1, 2.5, "muitos"])
def test_rejeita_quantidade_invalida(catalogo, quantidade):
    with pytest.raises(PropostaInvalida):
        validar_proposta({"mensagem": "ok", "itens": [{"produto_id": 80, "quantidade": quantidade}]}, catalogo)


def test_proposta_sem_itens_e_valida(catalogo):
    proposta = validar_proposta({"mensagem": "Nada atende", "itens": [], "pendencias": "sem tablets"}, catalogo)
    assert proposta.itens == []
    assert proposta.valor_total == Decimal("0.00")
    assert proposta.pendencias == "sem tablets"


def test_detalhe_mescla_com_resultado_de_busca():
    cat: dict = {}
    registrar_produtos(cat, {"produtos": [{"id": 1, "titulo": "A", "preco": 10}]})
    registrar_produtos(cat, {"id": 1, "titulo": "A", "preco": 10, "estoque": 5, "descricao": "d"})
    assert cat[1]["estoque"] == 5 and cat[1]["descricao"] == "d"
