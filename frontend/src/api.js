// Cliente HTTP do backend. Toda falha vira um ErroApi com mensagem pronta para o chat.

const BASE = '/api'
// Hierarquia de prazos: agente no backend (AGENT_TIMEOUT_SECONDS=120s) < este (150s) < nginx (300s).
// Assim o backend sempre responde (com sucesso ou erro claro) antes de o navegador desistir.
const TIMEOUT_MS = 150_000

export class ErroApi extends Error {
  constructor(mensagem, codigo = 'erro') {
    super(mensagem)
    this.codigo = codigo
  }
}

async function requisitar(caminho, opcoes = {}, fetchImpl = fetch) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), opcoes.timeoutMs ?? TIMEOUT_MS)
  let resp
  try {
    resp = await fetchImpl(`${BASE}${caminho}`, { ...opcoes, signal: controller.signal })
  } catch (e) {
    if (e?.name === 'AbortError') {
      throw new ErroApi('O servidor demorou demais para responder. Tente novamente.', 'timeout')
    }
    throw new ErroApi('Não foi possível conectar ao servidor. Verifique se o backend está rodando.', 'rede')
  } finally {
    clearTimeout(timer)
  }

  let corpo = null
  try {
    corpo = await resp.json()
  } catch {
    // corpo vazio ou não-JSON (ex.: 502 do proxy enquanto o backend sobe)
  }

  if (!resp.ok) {
    if (corpo?.mensagem) throw new ErroApi(corpo.mensagem, corpo.erro)
    if (resp.status === 502 || resp.status === 503 || resp.status === 504) {
      throw new ErroApi('O backend está indisponível no momento. Tente novamente em instantes.', 'indisponivel')
    }
    throw new ErroApi(`Erro inesperado do servidor (HTTP ${resp.status}).`, 'http')
  }
  if (corpo === null) throw new ErroApi('O servidor devolveu uma resposta inválida.', 'resposta_invalida')
  return corpo
}

export function enviarPedido(pedido, fetchImpl) {
  return requisitar(
    '/requisicoes',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pedido }) },
    fetchImpl,
  )
}

export function listarRequisicoes(fetchImpl) {
  return requisitar('/requisicoes', { timeoutMs: 15_000 }, fetchImpl)
}

const fmt = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD' })

// Valores chegam como string decimal ("5999.97"); Number() só para exibir.
export function formatarDinheiro(valor) {
  return fmt.format(Number(valor))
}
