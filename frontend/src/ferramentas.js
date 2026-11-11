// Transforma o rastro de ferramentas devolvido pelo backend (RespostaPedido.ferramentas)
// em linhas legíveis para o chat. Função pura: testável sem DOM.

const ERROS = {
  proposta_invalida: 'proposta rejeitada pela validação; o agente corrigiu',
  produto_nao_encontrado: 'produto não encontrado',
  argumentos_invalidos: 'argumentos inválidos',
  ferramenta_desconhecida: 'ferramenta inexistente',
  catalogo_timeout: 'catálogo não respondeu a tempo',
  catalogo_indisponivel: 'catálogo indisponível',
}

function qtdItens(args) {
  const n = Array.isArray(args?.itens) ? args.itens.length : 0
  return n === 1 ? '1 item' : `${n} itens`
}

function detalhe(ferramenta, args) {
  if (!args || typeof args !== 'object') return ''
  switch (ferramenta) {
    case 'buscar_produtos':
      return `"${args.termo ?? ''}"`
    case 'detalhar_produto':
      return `produto #${args.id ?? '?'}`
    case 'concluir_pedido':
      return qtdItens(args)
    default:
      return ''
  }
}

export function descreverChamada(chamada) {
  const { ferramenta, argumentos, sucesso, erro } = chamada ?? {}
  return {
    ferramenta: ferramenta || '(sem nome)',
    detalhe: detalhe(ferramenta, argumentos),
    sucesso: Boolean(sucesso),
    erro: sucesso ? null : ERROS[erro] ?? erro ?? 'falhou',
  }
}

export function descreverFerramentas(ferramentas) {
  return Array.isArray(ferramentas) ? ferramentas.map(descreverChamada) : []
}
