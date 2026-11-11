import { useEffect, useRef, useState } from 'react'
import { enviarPedido, formatarDinheiro, listarRequisicoes } from './api.js'
import { descreverFerramentas } from './ferramentas.js'

const EXEMPLOS = [
  'Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada.',
  'Quero 2 smartphones para a equipe de campo, orçamento total de US$ 1.000.',
  'Preciso de 10 laptops até US$ 500 cada.',
]

function TabelaItens({ requisicao }) {
  return (
    <table className="itens">
      <thead>
        <tr>
          <th>Produto</th>
          <th className="num">Qtd.</th>
          <th className="num">Preço unit.</th>
          <th className="num">Subtotal</th>
        </tr>
      </thead>
      <tbody>
        {requisicao.itens.map((item) => (
          <tr key={item.produto_id}>
            <td>
              <div className="produto">{item.titulo}</div>
              <div className="meta">
                #{item.produto_id}
                {item.marca ? ` · ${item.marca}` : ''}
                {item.categoria ? ` · ${item.categoria}` : ''}
              </div>
              {item.justificativa && <div className="justificativa">{item.justificativa}</div>}
            </td>
            <td className="num">{item.quantidade}</td>
            <td className="num">{formatarDinheiro(item.preco_unitario)}</td>
            <td className="num">{formatarDinheiro(item.subtotal)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td colSpan={3}>Valor total</td>
          <td className="num total">{formatarDinheiro(requisicao.valor_total)}</td>
        </tr>
      </tfoot>
    </table>
  )
}

function FerramentasUsadas({ ferramentas }) {
  const linhas = descreverFerramentas(ferramentas)
  if (linhas.length === 0) return null
  return (
    <details className="ferramentas">
      <summary>Ferramentas usadas ({linhas.length})</summary>
      <ol>
        {linhas.map((l, i) => (
          <li key={i} className={l.sucesso ? 'ok' : 'falha'}>
            <span aria-hidden="true">{l.sucesso ? '✓' : '✗'}</span> <code>{l.ferramenta}</code>
            {l.detalhe && <span className="meta"> {l.detalhe}</span>}
            {l.erro && <span className="meta"> ({l.erro})</span>}
          </li>
        ))}
      </ol>
    </details>
  )
}

function RespostaAgente({ resposta }) {
  const { status, mensagem, pendencias, requisicao, ferramentas } = resposta
  return (
    <div className="bolha agente">
      {status === 'criada' && requisicao && (
        <div className="cabecalho-req">
          <span className="selo ok">Requisição criada</span>
          <strong className="numero">{requisicao.numero}</strong>
        </div>
      )}
      {status === 'nao_atendida' && <span className="selo aviso">Nenhuma requisição criada</span>}
      <p>{mensagem}</p>
      {requisicao && <TabelaItens requisicao={requisicao} />}
      {pendencias && (
        <div className="pendencias">
          <strong>O que não foi atendido:</strong> {pendencias}
        </div>
      )}
      <FerramentasUsadas ferramentas={ferramentas} />
    </div>
  )
}

function Mensagem({ msg }) {
  if (msg.tipo === 'usuario') return <div className="bolha usuario">{msg.texto}</div>
  if (msg.tipo === 'erro')
    return (
      <div className="bolha erro" role="alert">
        <strong>Não foi possível concluir o pedido.</strong>
        <p>{msg.texto}</p>
      </div>
    )
  return <RespostaAgente resposta={msg.resposta} />
}

function Historico({ atualizarEm }) {
  const [estado, setEstado] = useState({ carregando: true, erro: null, itens: [] })

  useEffect(() => {
    // Sem setState síncrono aqui: a lista atual fica na tela até a nova chegar.
    let ativo = true
    listarRequisicoes()
      .then((itens) => ativo && setEstado({ carregando: false, erro: null, itens }))
      .catch((e) => ativo && setEstado({ carregando: false, erro: e.message, itens: [] }))
    return () => {
      ativo = false
    }
  }, [atualizarEm])

  return (
    <aside className="historico">
      <h2>Requisições salvas</h2>
      {estado.carregando && <p className="meta">Carregando…</p>}
      {estado.erro && <p className="erro-texto">{estado.erro}</p>}
      {!estado.carregando && !estado.erro && estado.itens.length === 0 && (
        <p className="meta">Nenhuma requisição ainda.</p>
      )}
      <ul>
        {estado.itens.map((r) => (
          <li key={r.id}>
            <div className="linha">
              <strong>{r.numero}</strong>
              <span>{formatarDinheiro(r.valor_total)}</span>
            </div>
            <div className="meta">
              {r.itens.map((i) => `${i.quantidade}× ${i.titulo}`).join(', ')}
            </div>
            <div className="meta">{new Date(r.criado_em).toLocaleString('pt-BR')}</div>
          </li>
        ))}
      </ul>
    </aside>
  )
}

export default function App() {
  const [mensagens, setMensagens] = useState([])
  const [texto, setTexto] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [versaoHistorico, setVersaoHistorico] = useState(0)
  const fimRef = useRef(null)

  useEffect(() => {
    fimRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [mensagens, enviando])

  async function enviar(pedido) {
    const conteudo = pedido.trim()
    if (!conteudo || enviando) return
    setTexto('')
    setEnviando(true)
    setMensagens((m) => [...m, { tipo: 'usuario', texto: conteudo }])
    try {
      const resposta = await enviarPedido(conteudo)
      setMensagens((m) => [...m, { tipo: 'agente', resposta }])
      if (resposta.requisicao) setVersaoHistorico((v) => v + 1)
    } catch (e) {
      setMensagens((m) => [...m, { tipo: 'erro', texto: e.message }])
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="layout">
      <main className="chat">
        <header>
          <h1>Agente de Compras</h1>
          <p className="meta">
            Descreva o que você precisa. O agente consulta o catálogo e gera a requisição de compra. Preços em
            dólares (USD).
          </p>
        </header>

        <section className="mensagens" aria-live="polite">
          {mensagens.length === 0 && (
            <div className="vazio">
              <p>Experimente um destes pedidos:</p>
              {EXEMPLOS.map((ex) => (
                <button key={ex} className="exemplo" onClick={() => enviar(ex)} disabled={enviando}>
                  {ex}
                </button>
              ))}
            </div>
          )}
          {mensagens.map((msg, i) => (
            <Mensagem key={i} msg={msg} />
          ))}
          {enviando && <div className="bolha agente carregando">Consultando o catálogo e montando a requisição…</div>}
          <div ref={fimRef} />
        </section>

        <form
          className="entrada"
          onSubmit={(e) => {
            e.preventDefault()
            enviar(texto)
          }}
        >
          <textarea
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                enviar(texto)
              }
            }}
            placeholder="Ex.: Preciso de 3 laptops para o time de vendas, até US$ 2.000 cada."
            rows={2}
            maxLength={2000}
            disabled={enviando}
            aria-label="Pedido de compra"
          />
          <button type="submit" disabled={enviando || texto.trim().length < 3}>
            {enviando ? 'Enviando…' : 'Enviar'}
          </button>
        </form>
      </main>
      <Historico atualizarEm={versaoHistorico} />
    </div>
  )
}
