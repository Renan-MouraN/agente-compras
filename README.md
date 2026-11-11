# Agente de Compras

A pessoa descreve o que precisa num chat. Um agente (LLM via OpenRouter) consulta o catálogo da
[DummyJSON](https://dummyjson.com) por meio de um **servidor MCP**, escolhe os itens e o backend grava uma
**requisição de compra** no Postgres. A resposta traz número, itens, quantidades, preços e valor total.

**Em resumo:**
- O modelo decide quais ferramentas chamar, sem fluxo fixo.
- O backend só grava o que confere com o catálogo consultado: IDs vistos, preço do catálogo, estoque e mínimo, totais calculados com `Decimal`.
- Falhas e lentidão de DummyJSON, MCP, LLM ou banco viram mensagens claras no chat, com prazo máximo por pedido.
- Dependências com versões fixas, para o build ser reproduzível.

## Como rodar

Pré-requisito: Docker com Docker Compose v2.

```bash
cp .env.example .env          # preencha OPENROUTER_API_KEY
docker compose up --build     # na primeira vez, `docker compose up` também faz o build
```

Os quatro serviços sobem em ordem, guiados por healthchecks: `postgres` e `mcp-server` ficam `healthy`,
depois sobe o `backend` (`healthy`) e então o `frontend`. Confira com `docker compose ps`. Para recomeçar
do zero, apagando o banco: `docker compose down -v`.

| Serviço | URL |
|---|---|
| Chat (React) | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| Servidor MCP (streamable HTTP) | http://localhost:8001/mcp |

As tabelas são criadas automaticamente quando o backend sobe. Para testar sem o navegador:

```bash
curl -s -X POST localhost:8000/requisicoes -H 'Content-Type: application/json' \
  -d '{"pedido": "Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada."}'
curl -s localhost:8000/requisicoes
```

Exemplo de resposta (resumida):

```json
{
  "status": "criada",
  "mensagem": "Requisição de 3 laptops Huawei Matebook X Pro: melhor avaliação dentro do limite de US$ 2.000.",
  "requisicao": {
    "numero": "REQ-000001",
    "valor_total": "4199.97",
    "moeda": "USD",
    "itens": [{ "produto_id": 80, "titulo": "Huawei Matebook X Pro", "quantidade": 3,
                "preco_unitario": "1399.99", "subtotal": "4199.97" }]
  },
  "ferramentas": [{ "ferramenta": "buscar_produtos", "argumentos": { "termo": "laptop" }, "sucesso": true },
                  { "ferramenta": "concluir_pedido", "...": "..." }]
}
```

## Arquitetura

```
 Navegador ──► frontend (nginx :3000) ──/api──► backend (FastAPI :8000) ──► OpenRouter (LLM)
                                                   │        │
                                     MCP (HTTP)    │        └──► Postgres (requisicoes, itens_requisicao)
                                                   ▼
                                   mcp-server (:8001) ──► DummyJSON
                                   buscar_produtos(termo)   → GET /products/search?q=
                                   detalhar_produto(id)     → GET /products/{id}
```

Fluxo de um pedido (`POST /requisicoes`):

1. O backend abre uma sessão MCP e lê as ferramentas com `list_tools`. Os schemas vêm do servidor MCP, não ficam duplicados no backend.
2. Envia ao LLM o prompt de sistema, o pedido e as ferramentas do MCP, mais `concluir_pedido` (ferramenta local de saída).
3. **Ciclo de tool calling:** o modelo escolhe qual ferramenta chamar, com quais argumentos e quantas vezes. O backend executa, devolve o resultado e repete. Não existe sequência fixa no código.
4. Quando o modelo chama `concluir_pedido` com IDs e quantidades, o backend valida a proposta contra o catálogo consultado, calcula os valores e grava tudo numa transação.

## Principais decisões

**O LLM escolhe; o backend confere e calcula.** O modelo informa apenas `produto_id` e `quantidade`.
Título, preço, estoque e pedido mínimo saem dos resultados que as ferramentas MCP devolveram *naquela
execução*. Antes de gravar, o backend aplica estas regras:

- rejeita IDs que não vieram das ferramentas, o que impede produto inventado;
- ignora qualquer preço que o modelo tente informar e usa o do catálogo;
- confere estoque, pedido mínimo (`minimumOrderQuantity`), duplicidade e os limites que o modelo extraiu do pedido (preço unitário máximo, orçamento total);
- calcula subtotal e total com `Decimal`, persistidos como `NUMERIC(…, 2)` e expostos na API como string (`"5999.97"`).

Se a proposta for inválida, os problemas voltam ao modelo como resultado da ferramenta, para ele corrigir.
Se nada atender ao pedido, o modelo conclui sem itens, explica o que falta em `pendencias` e nenhuma
requisição é gravada.

**Por que `concluir_pedido` é local e não uma ferramenta MCP.** O MCP é o catálogo: somente leitura,
sem estado e testável sem banco. Gravar a requisição é responsabilidade do backend, que valida e persiste
numa transação. A variante com `criar_requisicao` no MCP é um diferencial opcional do case e ficou fora,
de propósito, do escopo obrigatório.

**Erros com mensagem clara, sem o agente improvisar.** Se a DummyJSON falhar ou demorar, a ferramenta
MCP retorna um erro estruturado (`{"erro": "catalogo_timeout", "mensagem": ...}`). O backend interrompe o
ciclo, porque sem catálogo o agente só poderia inventar, e responde com o código HTTP adequado. O chat
exibe a `mensagem`.

| Situação | HTTP | `erro` |
|---|---|---|
| DummyJSON não respondeu no prazo (`DUMMYJSON_TIMEOUT_SECONDS`) | 504 | `catalogo_timeout` |
| DummyJSON fora do ar / HTTP 5xx | 503 | `catalogo_indisponivel` |
| Servidor MCP inacessível / lento | 503 / 504 | `mcp_indisponivel` / `mcp_timeout` |
| Chave OpenRouter ausente | 503 | `llm_nao_configurado` |
| OpenRouter: chave inválida, sem créditos, rate limit, timeout | 502 / 504 | `llm_erro` / `llm_timeout` |
| OpenRouter inacessível (DNS, conexão recusada ou caída) após 3 tentativas | 502 | `llm_indisponivel` |
| Modelo não concluiu em `AGENT_MAX_STEPS` rodadas | 502 | `agente_sem_conclusao` |
| Pedido inteiro passou de `AGENT_TIMEOUT_SECONDS` (nada é gravado) | 504 | `agente_timeout` |
| Banco inacessível durante o pedido | 503 | `banco_indisponivel` |
| Pedido vazio ou longo demais | 422 | `pedido_invalido` |

**Prazos alinhados.** Cada chamada externa tem seu próprio timeout: DummyJSON (10 s) e OpenRouter (60 s por
rodada). Por cima deles há um prazo total por pedido: `AGENT_TIMEOUT_SECONDS`, de 120 s. Esse prazo fica
abaixo do timeout do chat (150 s), que fica abaixo do timeout do nginx (300 s). Assim, o backend sempre
responde antes de o navegador desistir. A gravação acontece só depois que o agente conclui dentro do
prazo: se o prazo estourar, nada é gravado e o usuário recebe uma mensagem clara.

**Prompt e ferramentas.** As descrições das ferramentas avisam que o catálogo está em inglês. Uma busca
por "notebook" ou "celular" não retorna nada; com o aviso, o modelo traduz para "laptop" ou "smartphone"
e tenta sinônimos antes de desistir. O prompt (em [backend/app/agent.py](backend/app/agent.py)) proíbe
inventar e substituir itens, explica o formato brasileiro de valores ("US$ 2.000" = 2000) e pede que o
modelo não faça perguntas, já que cada pedido é independente.

**Outras escolhas:**
- **Modelo padrão:** `anthropic/claude-haiku-5.5`, barato e bom em tool calling. Para trocar, defina `OPENROUTER_MODEL`. Usei `temperature: 0` para respostas mais estáveis.
- **Cliente OpenRouter:** chamadas HTTP diretas, sem framework de agentes. O ciclo tem cerca de 100 linhas e é fácil de ler e testar.
- **Instabilidade de rede com a OpenRouter:** falhas de *conexão* (DNS, recusa, conexão caída antes da resposta) são tentadas de novo até 3 vezes, com espera de 1 s e 2 s, e a causa vai para o log. Timeouts e erros HTTP (401, 402, 429) não são repetidos: repetir estouraria o prazo do agente ou não resolveria.
- **MCP:** SDK oficial (`mcp` 2.3) com transporte streamable HTTP *stateless*, cada uma das quatro peças em seu próprio container. A proteção contra DNS rebinding fica ativa, com lista de hosts permitidos.
- **Banco:** tabelas criadas com `create_all` no startup, como pede o case. Em produção eu usaria migrações (Alembic).
- **Primeira subida do Postgres:** o healthcheck usa `pg_isready -h 127.0.0.1`, que força TCP. Na primeira inicialização do volume, a imagem sobe um servidor temporário só no socket Unix, e sem TCP o healthcheck marcaria "pronto" cedo demais. Além disso, o backend repete a criação das tabelas em erros transitórios ("the database system is starting up", conexão recusada, DNS) e falha na hora em erros permanentes (senha errada, banco inexistente), com mensagem no log.
- **Dependências fixadas:** todas as versões Python, inclusive as transitivas, estão fixadas com `==` em `requirements*.txt`. São as versões resolvidas para Python 3.12 (a das imagens) e usadas nos testes. O frontend usa o `package-lock.json` com `npm ci`.
- **Pedidos sem memória:** cada mensagem do chat é um pedido independente, sem histórico de conversa. Isso mantém o contrato da API simples (`{"pedido": "..."}`).
- **Moeda:** os preços da DummyJSON estão em USD. O agente não inventa câmbio para limites em outra moeda.

**Limitações conhecidas:**
- **Lembrete ao modelo:** se o modelo responder em texto livre em vez de chamar `concluir_pedido`, o backend envia **um** lembrete. Se ele insistir, o texto é devolvido como resposta, sem gravar nada. Isso garante o formato de saída e não define quais ferramentas usar.
- **Limites de preço e orçamento:** o backend confere esses limites com os valores que o *modelo* extrai do pedido (`preco_unitario_maximo`, `orcamento_total_maximo`). Se o modelo deixar de informar um limite que o usuário citou, essa conferência não acontece. Estoque, mínimo, existência do produto e preço são sempre conferidos.
- **Busca limitada:** `buscar_produtos` devolve até 20 resultados, sem paginação. O campo `total_encontrado` informa quando existem mais.

## Testes

Nenhum teste precisa de chave de LLM nem de internet: a DummyJSON é simulada com `respx` e o LLM é roteirizado.

```bash
# Servidor MCP: um ou mais testes por ferramenta (sucesso, vazio, timeout, 404, falha de conexão)
docker compose run --rm --no-deps mcp-server pytest -v

# Backend: validação/cálculos, ciclo do agente, API (SQLite temporário), prazos,
# retentativa do banco e erros de integração
docker compose run --rm --no-deps backend pytest -v

# Frontend: cliente da API e exibição das ferramentas rodam no build da imagem (npm test)
```

Sem Docker (Node 22+ e Python 3.12 ou superior; as imagens usam 3.12 e as suítes também foram executadas em 3.14):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r mcp_server/requirements-dev.txt -r backend/requirements-dev.txt
(cd mcp_server && pytest)            # 10 testes
(cd mcp_server && pytest -m live)    # opcional: contra a DummyJSON real
(cd backend && pytest)               # 81 testes (8 deles sobem o servidor MCP real)
(cd frontend && npm ci && npm test)  # 8 testes
ruff check . && (cd frontend && npm run lint)   # lint, como no CI (pip install ruff==0.17.4)
```

O **teste de contrato** ([backend/tests/test_contrato_mcp.py](backend/tests/test_contrato_mcp.py)) sobe o
servidor MCP real como subprocesso, com transporte HTTP, apontando para uma DummyJSON simulada local. Ele
exercita o cliente MCP do backend sem dublês: descoberta das ferramentas, saída estruturada, códigos de
erro, o agente e a API. Como depende da pasta `mcp_server/`, roda no ambiente local. Dentro do container do
backend ele é pulado, com o motivo explícito (8 testes `skipped`).

### Simulando falhas

```bash
# DummyJSON "lenta": aponta para um IP que não responde e reduz o timeout
DUMMYJSON_BASE_URL=http://10.255.255.1 DUMMYJSON_TIMEOUT_SECONDS=3 docker compose up -d mcp-server
# servidor MCP fora do ar
docker compose stop mcp-server
# volta ao normal
docker compose up -d mcp-server
```

## Diferenciais

### Ferramentas no chat

Cada resposta do agente tem um bloco recolhível **"Ferramentas usadas (N)"**, com as chamadas na ordem em
que o modelo as fez: `buscar_produtos "laptop"`, `detalhar_produto produto #80`, `concluir_pedido 1 item`.
As chamadas que falharam aparecem com ✗ e o motivo, por exemplo "proposta rejeitada pela validação; o
agente corrigiu". O backend já devolvia esse rastro no campo `ferramentas` da resposta. A mudança foi só no
chat ([frontend/src/ferramentas.js](frontend/src/ferramentas.js)).

### Script de avaliação (5 pedidos)

[backend/scripts/avaliar.py](backend/scripts/avaliar.py) envia 5 pedidos à API em execução, com LLM, MCP e
DummyJSON reais, e mostra quantos o agente acertou:

```bash
docker compose exec backend python scripts/avaliar.py   # ou, do host: python3 backend/scripts/avaliar.py
```

| # | Pedido | Acerto significa |
|---|---|---|
| 1 | 3 laptops até US$ 2.000 cada | requisição só com laptops, 3 unidades, preço unitário ≤ 2000 |
| 2 | 2 smartphones, orçamento total de US$ 1.000 | só smartphones, 2 unidades, total ≤ 1000 |
| 3 | 10 laptops até US$ 500 cada | **nenhuma** requisição (o laptop mais barato custa 1099,99) e uma explicação |
| 4 | 10 perfumes Dior J'adore | produto #8, 10 unidades, total exato de US$ 899,90 (preço vindo do catálogo) |
| 5 | 5 tratores agrícolas | **nenhuma** requisição (não há no catálogo) e nenhuma substituição |

Em todos os casos o agente também precisa ter consultado o catálogo. A saída lista as checagens, as
ferramentas chamadas e termina com "o agente acertou X de 5 pedidos". O código de saída é 0 só com 5 de 5.
Os pedidos atendidos são gravados como requisições normais. A lógica de correção tem testes próprios, sem
LLM ([backend/tests/test_avaliacao.py](backend/tests/test_avaliacao.py)).

### CI (GitHub Actions)

[.github/workflows/ci.yml](.github/workflows/ci.yml) roda em cada push na `main` e em pull requests, sem
precisar da chave da OpenRouter:

| Job | O que faz |
|---|---|
| Lint | `ruff check` no Python ([ruff.toml](ruff.toml)) e ESLint no chat ([frontend/eslint.config.js](frontend/eslint.config.js)) |
| Servidor MCP | `pytest` |
| Backend | `pytest`, incluindo o teste de contrato com o servidor MCP real |
| Chat | `npm test` e `npm run build` |
| docker compose | `config`, `up --build --wait` (healthchecks), `/health`, `GET /api/requisicoes` pelo nginx (prova que as tabelas foram criadas) e os testes dentro dos containers |

O `npm audit` aponta 4 alertas moderados, todos no ESLint, que só existe em desenvolvimento (`minimatch`,
ReDoS em padrões de glob). O código de produção tem 0 vulnerabilidades (`npm audit --omit=dev`).

## Estrutura

```
docker-compose.yml       # sobe as 4 peças
.env.example             # variáveis (copie para .env)
mcp_server/app/server.py      # ferramentas MCP + /health
mcp_server/app/dummyjson.py   # cliente HTTP com erros tipados
backend/app/agent.py          # prompt + ciclo de tool calling
backend/app/validacao.py      # regras anti-invenção e cálculo com Decimal
backend/app/mcp_client.py     # descoberta/execução de ferramentas MCP
backend/app/llm.py            # cliente OpenRouter
backend/app/main.py           # rotas FastAPI e tratamento de erros
backend/app/models.py         # tabelas requisicoes / itens_requisicao
backend/app/db.py             # engine, criação das tabelas e retentativa no startup
frontend/src/App.jsx          # chat
frontend/src/api.js           # cliente HTTP com mensagens de erro
frontend/src/ferramentas.js   # rastro de ferramentas exibido no chat
backend/scripts/avaliar.py    # avaliação com 5 pedidos de teste
.github/workflows/ci.yml      # CI: lint, testes e docker compose up
```

## Segurança

- A chave da OpenRouter só existe no `.env`, que não é versionado (`.gitignore`). Ela é lida pelo backend e nunca vai ao frontend nem às respostas da API.
- O Postgres não é exposto fora da rede do compose. As credenciais padrão servem só para o ambiente local.
- Os containers Python rodam como usuário não-root.
- A API (8000) e o servidor MCP (8001) ficam publicados no host **sem autenticação**, para facilitar os testes locais. Num ambiente real, só o frontend seria exposto, atrás de autenticação.
- Atenção: `docker compose config`, sem `--quiet`, imprime as variáveis já resolvidas, **inclusive a chave**. Evite rodar esse comando ao compartilhar a tela.

## Uso de IA

O desenvolvimento foi feito com o **Claude Code** (modelo Claude Opus 5.5) como par de programação. Eu
defini a stack, a arquitetura e as regras (o modelo decide as ferramentas, nada de produtos ou preços
inventados, validação antes de salvar) e pedi a implementação em etapas: estrutura e Docker, servidor
MCP, agente e persistência, frontend. Cada etapa foi acompanhada de testes.

A IA escreveu grande parte do código e dos testes. Antes de escrever, ela inspecionou a API real do SDK
MCP 2.x e os dados reais da DummyJSON. Também rodou o fluxo de ponta a ponta com a OpenRouter (pedido de
exemplo, pedido impossível, termo em português, pedido fora do escopo, falhas da DummyJSON e do MCP).
Revisei o código, as decisões e este README.
