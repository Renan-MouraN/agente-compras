"""Avaliação do agente: envia 5 pedidos de teste à API e mostra quantos o agente acertou.

Usa o stack em execução (LLM real via OpenRouter, MCP e DummyJSON reais), exatamente
como o chat. Só depende da biblioteca padrão:

    python3 backend/scripts/avaliar.py                       # do host, com o compose no ar
    docker compose exec backend python scripts/avaliar.py    # de dentro do container

Atenção: os pedidos atendidos são gravados no banco como requisições normais.
Código de saída: 0 se todos os casos passaram, 1 se algum falhou, 2 se a API não respondeu.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

Resposta = dict[str, Any]
Checagem = tuple[str, Callable[[Resposta], bool]]

FERRAMENTAS_DE_CATALOGO = {"buscar_produtos", "detalhar_produto"}


# --------------------------------------------------------------------------- checagens


def _itens(r: Resposta) -> list[dict[str, Any]]:
    return ((r.get("requisicao") or {}).get("itens")) or []


def criada(r: Resposta) -> bool:
    return r.get("status") == "criada" and bool(r.get("requisicao"))


def nao_atendida(r: Resposta) -> bool:
    return r.get("status") == "nao_atendida" and not r.get("requisicao")


def consultou_catalogo(r: Resposta) -> bool:
    return any(c.get("ferramenta") in FERRAMENTAS_DE_CATALOGO and c.get("sucesso") for c in r.get("ferramentas") or [])


def so_categoria(categoria: str) -> Callable[[Resposta], bool]:
    return lambda r: bool(_itens(r)) and all(i.get("categoria") == categoria for i in _itens(r))


def quantidade_total(n: int) -> Callable[[Resposta], bool]:
    return lambda r: sum(int(i.get("quantidade", 0)) for i in _itens(r)) == n


def preco_unitario_ate(limite: str) -> Callable[[Resposta], bool]:
    return lambda r: bool(_itens(r)) and all(Decimal(i["preco_unitario"]) <= Decimal(limite) for i in _itens(r))


def total_ate(limite: str) -> Callable[[Resposta], bool]:
    return lambda r: criada(r) and Decimal(r["requisicao"]["valor_total"]) <= Decimal(limite)


def total_igual(valor: str) -> Callable[[Resposta], bool]:
    return lambda r: criada(r) and Decimal(r["requisicao"]["valor_total"]) == Decimal(valor)


def produtos(*ids: int) -> Callable[[Resposta], bool]:
    return lambda r: sorted(int(i["produto_id"]) for i in _itens(r)) == sorted(ids)


# --------------------------------------------------------------------------- casos


@dataclass(frozen=True)
class Caso:
    nome: str
    pedido: str
    checagens: list[Checagem] = field(default_factory=list)


CASOS: list[Caso] = [
    Caso(
        "Laptops com teto por unidade",
        "Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada.",
        [
            ("requisição criada", criada),
            ("consultou o catálogo", consultou_catalogo),
            ("só laptops", so_categoria("laptops")),
            ("3 unidades no total", quantidade_total(3)),
            ("preço unitário ≤ US$ 2.000", preco_unitario_ate("2000")),
        ],
    ),
    Caso(
        "Smartphones com orçamento total",
        "Quero 2 smartphones para a equipe de campo, orçamento total de US$ 1.000.",
        [
            ("requisição criada", criada),
            ("consultou o catálogo", consultou_catalogo),
            ("só smartphones", so_categoria("smartphones")),
            ("2 unidades no total", quantidade_total(2)),
            ("total ≤ US$ 1.000", total_ate("1000")),
        ],
    ),
    Caso(
        "Restrição impossível (não substituir)",
        "Preciso de 10 laptops até US$ 500 cada.",
        [
            ("nenhuma requisição gravada", nao_atendida),
            ("consultou o catálogo", consultou_catalogo),
            ("explicou o que faltou", lambda r: bool((r.get("pendencias") or r.get("mensagem") or "").strip())),
        ],
    ),
    Caso(
        "Produto específico (tradução + preço do catálogo)",
        "Quero 10 unidades do perfume Dior J'adore para brindes de fim de ano.",
        [
            ("requisição criada", criada),
            ("consultou o catálogo", consultou_catalogo),
            ("produto #8 (Dior J'adore)", produtos(8)),
            ("10 unidades", quantidade_total(10)),
            ("total exato de US$ 899,90", total_igual("899.90")),
        ],
    ),
    Caso(
        "Item fora do catálogo",
        "Preciso de 5 tratores agrícolas para a fazenda.",
        [
            ("nenhuma requisição gravada", nao_atendida),
            ("consultou o catálogo", consultou_catalogo),
        ],
    ),
]


# --------------------------------------------------------------------------- execução


@dataclass
class Resultado:
    caso: Caso
    checagens: list[tuple[str, bool]]
    erro: str | None = None
    segundos: float = 0.0
    ferramentas: list[str] = field(default_factory=list)
    numero: str | None = None

    @property
    def acertou(self) -> bool:
        return self.erro is None and all(ok for _, ok in self.checagens)


def avaliar_resposta(caso: Caso, resposta: Resposta) -> list[tuple[str, bool]]:
    """Aplica as checagens do caso a uma resposta 200/201 da API. Função pura (testável sem LLM)."""
    resultado = []
    for descricao, checar in caso.checagens:
        try:
            ok = bool(checar(resposta))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            ok = False
        resultado.append((descricao, ok))
    return resultado


class ApiInacessivel(Exception):
    pass


def enviar(url: str, pedido: str, timeout: float) -> tuple[int, Resposta]:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/requisicoes",
        data=json.dumps({"pedido": pedido}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            corpo = json.loads(exc.read() or b"{}")
        except ValueError:
            corpo = {}
        return exc.code, corpo
    except (urllib.error.URLError, OSError) as exc:  # conexão recusada, DNS, timeout
        raise ApiInacessivel(str(getattr(exc, "reason", exc))) from exc


def executar_caso(caso: Caso, url: str, timeout: float) -> Resultado:
    inicio = time.monotonic()
    status, corpo = enviar(url, caso.pedido, timeout)
    segundos = time.monotonic() - inicio
    ferramentas = [c.get("ferramenta", "?") for c in corpo.get("ferramentas") or []]
    if status not in (200, 201):
        erro = f"HTTP {status} {corpo.get('erro', '')}: {corpo.get('mensagem', '')}".strip()
        return Resultado(caso, [], erro=erro, segundos=segundos)
    numero = (corpo.get("requisicao") or {}).get("numero")
    return Resultado(caso, avaliar_resposta(caso, corpo), segundos=segundos, ferramentas=ferramentas, numero=numero)


def imprimir(i: int, r: Resultado) -> None:
    marca = "✓" if r.acertou else "✗"
    extra = f" → {r.numero}" if r.numero else ""
    print(f"\n[{i}] {marca} {r.caso.nome} ({r.segundos:.1f}s){extra}")
    print(f"    pedido: {r.caso.pedido}")
    if r.erro:
        print(f"    erro: {r.erro}")
    for descricao, ok in r.checagens:
        print(f"    {'✓' if ok else '✗'} {descricao}")
    if r.ferramentas:
        print(f"    ferramentas: {' → '.join(r.ferramentas)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.getenv("AVALIACAO_URL", "http://localhost:8000"), help="URL do backend")
    parser.add_argument("--timeout", type=float, default=150.0, help="segundos por pedido (padrão: 150)")
    args = parser.parse_args(argv)

    print(f"Avaliando o agente em {args.url} com {len(CASOS)} pedidos de teste…")
    resultados = []
    for i, caso in enumerate(CASOS, 1):
        try:
            r = executar_caso(caso, args.url, args.timeout)
        except ApiInacessivel as exc:
            print(f"\nNão foi possível falar com a API em {args.url}: {exc}")
            print("Suba o stack com `docker compose up` (ou informe --url).")
            return 2
        resultados.append(r)
        imprimir(i, r)

    acertos = sum(r.acertou for r in resultados)
    print(f"\nResultado: o agente acertou {acertos} de {len(resultados)} pedidos.")
    return 0 if acertos == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
