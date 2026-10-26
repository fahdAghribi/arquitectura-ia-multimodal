import { useEffect, useState } from 'react'
import { apiFetch, keycloak } from './auth'
import { useConsultaSession } from './session'
import AgentTrace from './components/AgentTrace'
import CameraCapture from './components/CameraCapture'
import Respuesta from './components/Respuesta'
import RevisionInbox from './components/RevisionInbox'
import VoiceInput from './components/VoiceInput'

const EJEMPLOS = [
  '¿Qué deducción autonómica por alquiler de vivienda habitual existe en Andalucía?',
  '¿Cómo se presenta el modelo 303 y qué IVA puedo deducir?',
  '¿Qué documentación me falta por entregar?',
  'Vendo a clientes particulares de otros países de la UE, ¿tengo que darme de alta en la ventanilla única?',
]

function Consultar({ me }) {
  const { sessionId, trace, turns, busy, error, ask, reset } = useConsultaSession()
  const [expedientes, setExpedientes] = useState([])
  const [expedienteId, setExpedienteId] = useState('')
  const [pregunta, setPregunta] = useState('')
  const [image, setImage] = useState(null)

  useEffect(() => {
    apiFetch('/api/v1/expedientes').then(setExpedientes).catch(() => setExpedientes([]))
  }, [])

  function submit(e) {
    e.preventDefault()
    ask({ pregunta, expedienteId, image: image ? { media_type: image.media_type, data: image.data } : null })
    setPregunta('')
    setImage(null)
  }

  return (
    <main className="layout">
      <form className="card" onSubmit={submit}>
        <div className="row between">
          <h2>Nueva consulta</h2>
          <button type="button" className="btn ghost small" onClick={reset} title={`Sesión ${sessionId.slice(0, 8)}`}>Nueva conversación</button>
        </div>
        <label className="field">
          Expediente
          <select value={expedienteId} onChange={(e) => setExpedienteId(e.target.value)}>
            <option value="">— Consulta general —</option>
            {expedientes.map((x) => (
              <option key={x.expediente_id} value={x.expediente_id}>
                {x.expediente_id} · {x.tramite} ({x.modelo}){me?.es_gestor ? ` · ${x.nombre}` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Su pregunta
          <textarea rows={4} value={pregunta} placeholder="Escriba o dicte su consulta sobre un trámite o impuesto" onChange={(e) => setPregunta(e.target.value)} />
        </label>
        <div className="ejemplos">
          {EJEMPLOS.map((t) => (
            <button type="button" key={t} className="chip" onClick={() => setPregunta(t)}>{t}</button>
          ))}
        </div>
        <CameraCapture image={image} onChange={setImage} />
        {image && <p className="muted small">La imagen se envía al modelo de IA tal cual: evite fotos con datos de terceros.</p>}
        <div className="row between">
          <VoiceInput onText={(t) => setPregunta((p) => (p ? `${p} ${t}` : t))} />
          <button className="btn primary" type="submit" disabled={busy || (!pregunta.trim() && !image)}>
            {busy ? 'Consultando…' : 'Enviar consulta'}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </form>

      <div className="results">
        <AgentTrace steps={trace} busy={busy} />
        {[...turns].reverse().map((t, i) => (
          <div key={`${t.session_id}-${turns.length - i}`} className="turn">
            {t.pregunta && <p className="pregunta">«{t.pregunta}»</p>}
            <Respuesta data={t} />
          </div>
        ))}
      </div>
    </main>
  )
}

export default function App() {
  const [me, setMe] = useState(null)
  const [tab, setTab] = useState('consultar')
  const [pending, setPending] = useState(0)

  useEffect(() => {
    apiFetch('/api/v1/me')
      .then((m) => {
        setMe(m)
        if (m.es_gestor) setTab('revision')
      })
      .catch(() => setMe({ username: keycloak.tokenParsed?.preferred_username }))
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <div className="row">
          <strong>Asistente de trámites tributarios</strong>
          {me?.es_gestor && (
            <nav className="tabs">
              <button className={tab === 'revision' ? 'active' : ''} onClick={() => setTab('revision')}>
                Revisión{pending ? ` (${pending})` : ''}
              </button>
              <button className={tab === 'consultar' ? 'active' : ''} onClick={() => setTab('consultar')}>Consultar</button>
            </nav>
          )}
        </div>
        <div className="row">
          {me && (
            <span className="small">
              {me.username} <span className={`tag ${me.es_gestor ? 'ok' : ''}`}>{me.es_gestor ? 'gestor' : 'cliente'}</span>
            </span>
          )}
          <button className="btn ghost" onClick={() => keycloak.logout({ redirectUri: window.location.origin })}>Salir</button>
        </div>
      </header>
      <p className="disclaimer">
        Servicio del despacho, <strong>no es un servicio oficial de la Agencia Tributaria</strong>. Las respuestas se basan en publicaciones
        informativas de la AEAT y {me?.es_gestor ? 'las de clientes las revisa un gestor' : 'las revisa su gestor'} antes de entregarse.
      </p>
      {me?.es_gestor && tab === 'revision' ? <RevisionInbox onCount={setPending} /> : <Consultar me={me} />}
    </div>
  )
}
