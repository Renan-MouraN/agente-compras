import { describe, expect, it } from 'vitest'
import { descreverChamada, descreverFerramentas } from '../ferramentas.js'

describe('descreverFerramentas', () => {
  it('descreve o rastro do agente na ordem em que as ferramentas foram chamadas', () => {
    const rastro = [
      { ferramenta: 'buscar_produtos', argumentos: { termo: 'laptop' }, sucesso: true, erro: null },
      { ferramenta: 'detalhar_produto', argumentos: { id: 80 }, sucesso: true, erro: null },
      { ferramenta: 'concluir_pedido', argumentos: { itens: [{ produto_id: 80, quantidade: 3 }] }, sucesso: true },
    ]
    expect(descreverFerramentas(rastro)).toEqual([
      { ferramenta: 'buscar_produtos', detalhe: '"laptop"', sucesso: true, erro: null },
      { ferramenta: 'detalhar_produto', detalhe: 'produto #80', sucesso: true, erro: null },
      { ferramenta: 'concluir_pedido', detalhe: '1 item', sucesso: true, erro: null },
    ])
  })

  it('traduz códigos de erro conhecidos e mantém os desconhecidos', () => {
    const rejeitada = { ferramenta: 'concluir_pedido', argumentos: { itens: [{}, {}] }, sucesso: false, erro: 'proposta_invalida' }
    expect(descreverChamada(rejeitada)).toMatchObject({ detalhe: '2 itens', sucesso: false, erro: expect.stringMatching(/rejeitada/) })
    expect(descreverChamada({ ferramenta: 'x', argumentos: '{quebrado', sucesso: false, erro: 'novo_codigo' })).toEqual({
      ferramenta: 'x', detalhe: '', sucesso: false, erro: 'novo_codigo',
    })
  })

  it('tolera respostas sem rastro (ex.: backend antigo)', () => {
    expect(descreverFerramentas(undefined)).toEqual([])
    expect(descreverFerramentas(null)).toEqual([])
  })
})
