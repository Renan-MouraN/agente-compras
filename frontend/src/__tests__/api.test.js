import { describe, expect, it } from 'vitest'
import { enviarPedido, ErroApi, formatarDinheiro } from '../api.js'

const resposta = (status, corpo) => async () => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => {
    if (corpo === undefined) throw new SyntaxError('sem corpo')
    return corpo
  },
})

describe('enviarPedido', () => {
  it('devolve o corpo em caso de sucesso', async () => {
    const corpo = { status: 'criada', mensagem: 'ok', requisicao: { numero: 'REQ-000001' } }
    await expect(enviarPedido('3 laptops', resposta(201, corpo))).resolves.toEqual(corpo)
  })

  it('usa a mensagem do backend quando há erro tratado', async () => {
    const fetchImpl = resposta(504, { erro: 'catalogo_timeout', mensagem: 'A DummyJSON não respondeu em 10s.' })
    const erro = await enviarPedido('3 laptops', fetchImpl).catch((e) => e)
    expect(erro).toBeInstanceOf(ErroApi)
    expect(erro.codigo).toBe('catalogo_timeout')
    expect(erro.message).toBe('A DummyJSON não respondeu em 10s.')
  })

  it('traduz 502 sem corpo (backend subindo) em mensagem clara', async () => {
    const erro = await enviarPedido('x', resposta(502)).catch((e) => e)
    expect(erro.message).toMatch(/indisponível/)
  })

  it('traduz falha de rede em mensagem clara', async () => {
    const fetchImpl = async () => {
      throw new TypeError('Failed to fetch')
    }
    const erro = await enviarPedido('x', fetchImpl).catch((e) => e)
    expect(erro.codigo).toBe('rede')
    expect(erro.message).toMatch(/conectar ao servidor/)
  })
})

describe('formatarDinheiro', () => {
  it('formata string decimal como USD em pt-BR', () => {
    expect(formatarDinheiro('5999.97').replace(/\s/g, ' ')).toBe('US$ 5.999,97')
  })
})
