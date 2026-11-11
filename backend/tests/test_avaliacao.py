"""Script de avaliação (scripts/avaliar.py): checagens e execução, sem LLM.

A API é simulada por um servidor HTTP local que devolve respostas prontas por pedido.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts import avaliar


def _item(pid, categoria, preco, qtd):
    return {"produto_id": pid, "titulo": f"P{pid}", "categoria": categoria, "marca": None,
            "preco_unitario": preco, "quantidade": qtd, "subtotal": "0", "justificativa": None}


def _criada(total, *itens, ferramentas=("buscar_produtos", "concluir_pedido")):
    return {
        "status": "criada", "mensagem": "ok", "pendencias": None,
        "requisicao": {"numero": "REQ-000001", "valor_total": total, "itens": list(itens)},
        "ferramentas": [{"ferramenta": f, "argumentos": {}, "sucesso": True, "erro": None} for f in ferramentas],
    }


def _nao_atendida(ferramentas=("buscar_produtos", "concluir_pedido")):
    return {
        "status": "nao_atendida", "mensagem": "Nada atende.", "pendencias": "O laptop mais barato custa US$ 1.099,99.",
        "requisicao": None,
        "ferramentas": [{"ferramenta": f, "argumentos": {}, "sucesso": True, "erro": None} for f in ferramentas],
    }


# Uma resposta "correta" por caso, na ordem de avaliar.CASOS.
CORRETAS = [
    _criada("4199.97", _item(80, "laptops", "1399.99", 3)),
    _criada("599.98", _item(131, "smartphones", "299.99", 2)),
    _nao_atendida(),
    _criada("899.90", _item(8, "fragrances", "89.99", 10)),
    _nao_atendida(),
]


def test_sao_cinco_casos_com_pedidos_distintos():
    assert len(avaliar.CASOS) == 5
    assert len({c.pedido for c in avaliar.CASOS}) == 5


@pytest.mark.parametrize("caso, resposta", list(zip(avaliar.CASOS, CORRETAS, strict=True)), ids=[c.nome for c in avaliar.CASOS])
def test_resposta_correta_passa_em_todas_as_checagens(caso, resposta):
    assert all(ok for _, ok in avaliar.avaliar_resposta(caso, resposta))


@pytest.mark.parametrize(
    "indice, resposta, checagem_que_falha",
    [
        # substituiu laptop por outra categoria
        (0, _criada("599.97", _item(80, "laptops", "1399.99", 2), _item(5, "beauty", "8.99", 1)), "só laptops"),
        # estourou o orçamento total
        (1, _criada("1399.98", _item(124, "smartphones", "899.99", 2)), "total ≤ US$ 1.000"),
        # inventou uma requisição em vez de recusar
        (2, _criada("10999.90", _item(81, "laptops", "1099.99", 10)), "nenhuma requisição gravada"),
        # produto errado
        (3, _criada("499.90", _item(6, "fragrances", "49.99", 10)), "produto #8 (Dior J'adore)"),
        # respondeu sem consultar o catálogo
        (4, _nao_atendida(ferramentas=("concluir_pedido",)), "consultou o catálogo"),
    ],
)
def test_resposta_errada_e_detectada(indice, resposta, checagem_que_falha):
    resultado = dict(avaliar.avaliar_resposta(avaliar.CASOS[indice], resposta))
    assert resultado[checagem_que_falha] is False


def test_resposta_malformada_conta_como_erro_e_nao_quebra():
    resultado = avaliar.avaliar_resposta(avaliar.CASOS[0], {"status": "criada", "requisicao": {"itens": [{}]}})
    assert not all(ok for _, ok in resultado)


# --------------------------------------------------------------------------- execução contra API simulada


@pytest.fixture
def api_simulada():
    respostas = {c.pedido: (201 if r["status"] == "criada" else 200, r) for c, r in zip(avaliar.CASOS, CORRETAS, strict=True)}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            corpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, dados = respostas[corpo["pedido"]]
            saida = json.dumps(dados).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(saida)))
            self.end_headers()
            self.wfile.write(saida)

        def log_message(self, *args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield respostas, f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


def test_main_mostra_quantos_o_agente_acertou(api_simulada, capsys):
    _, url = api_simulada
    assert avaliar.main(["--url", url]) == 0
    assert "o agente acertou 5 de 5 pedidos" in capsys.readouterr().out


def test_erro_http_conta_como_falha_com_a_mensagem_da_api(api_simulada, capsys):
    respostas, url = api_simulada
    respostas[avaliar.CASOS[0].pedido] = (503, {"erro": "catalogo_indisponivel", "mensagem": "DummyJSON fora do ar."})
    assert avaliar.main(["--url", url]) == 1
    saida = capsys.readouterr().out
    assert "HTTP 503 catalogo_indisponivel: DummyJSON fora do ar." in saida
    assert "o agente acertou 4 de 5 pedidos" in saida


def test_api_fora_do_ar_retorna_codigo_2(capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    assert avaliar.main(["--url", f"http://127.0.0.1:{porta}", "--timeout", "3"]) == 2
    assert "Não foi possível falar com a API" in capsys.readouterr().out
