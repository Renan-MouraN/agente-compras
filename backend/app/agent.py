"""Agente de compras: ciclo de tool calling entre o LLM (OpenRouter) e o servidor MCP.

O backend NÃO decide quais ferramentas chamar nem em que ordem. A cada rodada ele envia
ao modelo o histórico e a lista de ferramentas; executa as chamadas que o modelo pedir;
devolve os resultados; e repete até o modelo chamar `concluir_pedido` (ou estourar o
limite de rodadas).

`concluir_pedido` é uma ferramenta local (não MCP): é o "formulário de saída" do agente.
O modelo informa só IDs e quantidades; o backend valida contra o catálogo consultado e
calcula os valores. Se a validação falhar, os problemas voltam ao modelo para correção.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app import errors
from app.errors import AppError
from app.mcp_client import ResultadoFerramenta
from app.validacao import PropostaInvalida, PropostaValidada, registrar_produtos, validar_proposta

log = logging.getLogger(__name__)

CONCLUIR = "concluir_pedido"
ERROS_CATALOGO_FATAIS = {"catalogo_timeout", "catalogo_indisponivel"}

SYSTEM_PROMPT = """\
Você é um agente de compras corporativas. Você recebe um pedido em linguagem natural e o \
transforma em uma requisição de compra usando SOMENTE produtos do catálogo, consultado pelas ferramentas.

FERRAMENTAS
- buscar_produtos(termo): busca no catálogo. O catálogo está em INGLÊS: traduza o pedido \
(ex.: notebook -> laptop, celular -> smartphone, perfume -> fragrance). Se a busca vier vazia, \
tente sinônimos ou a categoria (no máximo 3 tentativas por item) antes de desistir.
- detalhar_produto(id): detalhes de um produto (use quando precisar confirmar algo do item).
- concluir_pedido(...): entrega o resultado final. Chame exatamente UMA vez, no fim.

REGRAS
1. Nunca invente produtos, IDs, preços ou estoque. Use apenas dados devolvidos pelas ferramentas nesta conversa.
2. Respeite as restrições do pedido: quantidade, preço máximo por unidade, orçamento total, marca, características.
3. Entre os produtos que atendem, escolha o mais adequado ao uso descrito (considere preço e avaliação) e \
justifique em uma frase curta.
4. A quantidade deve ser no máximo o estoque e no mínimo o pedido_minimo do produto. Sem quantidade no pedido, use 1 \
(ou o pedido mínimo, se for maior).
5. Se nada no catálogo atender a um item, NÃO o substitua por algo diferente: deixe-o fora e explique em \
`pendencias` o que falta (ex.: "nenhum laptop até US$ 500 no catálogo; o mais barato custa US$ 1.099,99").
6. Os preços do catálogo estão em dólares (USD). Valores no formato brasileiro: "US$ 2.000" = 2000; \
"1.500,50" = 1500.50. Se o limite vier em outra moeda (ex.: R$), não invente câmbio: avise em `pendencias`.
7. Cada pedido é independente e não há como fazer perguntas ao usuário. Se o pedido for ambíguo, adote a \
interpretação mais razoável e diga qual foi. Se não for um pedido de compra, chame concluir_pedido sem itens \
explicando o que você faz.
8. Não calcule totais: o sistema calcula os valores a partir do catálogo. Se concluir_pedido devolver \
problemas de validação, corrija a proposta (ou consulte mais produtos) e chame de novo.
9. Escreva `mensagem` e `pendencias` em português, de forma curta e objetiva.
"""

CONCLUIR_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": CONCLUIR,
        "description": (
            "Entrega o resultado final do pedido. Com itens, o sistema valida e grava a requisição de compra "
            "(preços e totais são calculados pelo sistema a partir do catálogo). Sem itens, informa que nada "
            "no catálogo atende ao pedido. Chame uma única vez, depois de consultar o catálogo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "itens": {
                    "type": "array",
                    "description": "Produtos escolhidos. Vazio se nada atender ao pedido.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "produto_id": {"type": "integer", "description": "ID retornado pelas ferramentas."},
                            "quantidade": {"type": "integer", "minimum": 1},
                            "justificativa": {"type": "string", "description": "Por que este produto (1 frase)."},
                        },
                        "required": ["produto_id", "quantidade"],
                    },
                },
                "mensagem": {
                    "type": "string",
                    "description": "Resposta curta ao usuário, em português, resumindo o que foi escolhido.",
                },
                "pendencias": {
                    "type": "string",
                    "description": "O que não pôde ser atendido e por quê. Omita se tudo foi atendido.",
                },
                "preco_unitario_maximo": {
                    "type": "number",
                    "description": "Limite de preço por unidade em USD, SOMENTE se o usuário informou.",
                },
                "orcamento_total_maximo": {
                    "type": "number",
                    "description": "Limite do valor total em USD, SOMENTE se o usuário informou.",
                },
            },
            "required": ["itens", "mensagem"],
        },
    },
}

LEMBRETE = (
    "Para entregar o resultado, chame a ferramenta concluir_pedido (com os itens escolhidos, ou sem itens "
    "explicando o que falta). Não responda em texto livre."
)


class LLM(Protocol):
    model: str

    async def completar(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


class Catalogo(Protocol):
    async def ferramentas_openai(self) -> list[dict[str, Any]]: ...

    async def chamar(self, nome: str, argumentos: dict[str, Any]) -> ResultadoFerramenta: ...


@dataclass
class ChamadaFerramenta:
    ferramenta: str
    argumentos: Any
    sucesso: bool
    erro: str | None = None


@dataclass
class ResultadoAgente:
    status: Literal["criada", "nao_atendida"]
    mensagem: str
    proposta: PropostaValidada | None = None
    ferramentas: list[ChamadaFerramenta] = field(default_factory=list)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _ordem(tc: dict[str, Any]) -> int:
    # Se o modelo pedir buscas e a conclusão na mesma rodada, executa as buscas primeiro.
    return 1 if tc.get("function", {}).get("name") == CONCLUIR else 0


async def executar_agente(pedido: str, llm: LLM, catalogo_mcp: Catalogo, max_steps: int) -> ResultadoAgente:
    ferramentas_mcp = await catalogo_mcp.ferramentas_openai()
    nomes_mcp = {t["function"]["name"] for t in ferramentas_mcp}
    tools = [*ferramentas_mcp, CONCLUIR_TOOL]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": pedido},
    ]
    catalogo: dict[int, dict[str, Any]] = {}  # produtos vistos nesta execução (fonte da verdade p/ validação)
    trace: list[ChamadaFerramenta] = []
    lembrete_enviado = False

    for passo in range(1, max_steps + 1):
        msg = await llm.completar(messages, tools)
        tool_calls = [tc for tc in (msg.get("tool_calls") or []) if isinstance(tc, dict)]
        log.info("passo %d: %d chamada(s) de ferramenta", passo, len(tool_calls))

        assistente: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
        for tc in tool_calls:
            tc.setdefault("id", f"call_{uuid.uuid4().hex[:12]}")
        if tool_calls:
            assistente["tool_calls"] = tool_calls
        messages.append(assistente)

        if not tool_calls:
            # O modelo respondeu em texto livre. Lembra uma vez de usar concluir_pedido;
            # se insistir, devolve o texto como resposta sem requisição.
            if not lembrete_enviado:
                lembrete_enviado = True
                messages.append({"role": "user", "content": LEMBRETE})
                continue
            texto = (msg.get("content") or "").strip() or "Não consegui montar uma requisição para este pedido."
            return ResultadoAgente(status="nao_atendida", mensagem=texto, ferramentas=trace)

        for tc in sorted(tool_calls, key=_ordem):
            nome = tc.get("function", {}).get("name", "")
            bruto = tc.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(bruto) if isinstance(bruto, str) else bruto
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                trace.append(ChamadaFerramenta(nome, bruto, False, "argumentos_invalidos"))
                conteudo = _json({"erro": "argumentos_invalidos", "mensagem": "Os argumentos devem ser um objeto JSON."})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": conteudo})
                continue

            if nome == CONCLUIR:
                try:
                    proposta = validar_proposta(args, catalogo)
                except PropostaInvalida as exc:
                    log.info("proposta rejeitada: %s", exc.problemas)
                    trace.append(ChamadaFerramenta(nome, args, False, "proposta_invalida"))
                    conteudo = _json({"erro": "proposta_invalida", "problemas": exc.problemas})
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": conteudo})
                    continue
                trace.append(ChamadaFerramenta(nome, args, True))
                status = "criada" if proposta.itens else "nao_atendida"
                return ResultadoAgente(status=status, mensagem=proposta.mensagem, proposta=proposta, ferramentas=trace)

            if nome in nomes_mcp:
                resultado = await catalogo_mcp.chamar(nome, args)
                trace.append(ChamadaFerramenta(nome, args, resultado.ok, resultado.erro_codigo))
                if resultado.ok:
                    registrar_produtos(catalogo, resultado.dados)
                elif resultado.erro_codigo in ERROS_CATALOGO_FATAIS:
                    # Sem catálogo não há como responder sem inventar: interrompe com mensagem clara.
                    raise errors.catalogo(resultado.erro_codigo, resultado.erro_mensagem or "Catálogo indisponível.")
                conteudo = resultado.texto
            else:
                trace.append(ChamadaFerramenta(nome, args, False, "ferramenta_desconhecida"))
                conteudo = _json({"erro": "ferramenta_desconhecida", "mensagem": f"Ferramenta '{nome}' não existe."})

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": conteudo})

    raise AppError(
        502,
        "agente_sem_conclusao",
        f"O agente não concluiu o pedido em {max_steps} etapas. Tente reformular o pedido de forma mais específica.",
    )
