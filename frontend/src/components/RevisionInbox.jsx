import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../auth'
import Respuesta from './Respuesta'

/** Bandeja del gestor: borradores de respuesta a clientes pendientes de aprobación (Human-in-the-Loop). */
export default function RevisionInbox({ onCount }) {
  const [items, setItems] = useState([])
  const [selected, setSelected] = useState(null)
  const [nota, setNota] = useState('')
  const [resumen, setResumen] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const data = await apiFetch('/api/v1/revisiones')
      setItems(data)
      onCount?.(data.length)
    } catch (e) {
      setError(e.message)
    }
  }, [onCount])

  useEffect(() => {
    load()
    const t = setInterval(load, 10_000)
    return () => clearInterval(t)
  }, [load])

  function open(item) {
    setSelected(item)
    setNota('')
    setResumen(item.respuesta?.resumen || '')
  }

  async function decide(aprobado) {
    setBusy(true)
    setError(null)
    try {
      await apiFetch(`/api/v1/revisiones/${selected.session_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aprobado, nota, resumen_editado: aprobado && resumen !== selected.respuesta?.resumen ? resumen : null }),
      })
      setSelected(null)
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="layout">
      <section className="card">
        <div className="row between">
          <h2>Pendientes de revisión ({items.length})</h2>
          <button className="btn ghost" onClick={load}>Actualizar</button>
        </div>
        {items.length === 0 && <p className="muted">No hay consultas pendientes.</p>}
        <ul className="inbox">
          {items.map((it) => (
            <li key={it.session_id}>
              <button className={`inbox-item ${selected?.session_id === it.session_id ? 'active' : ''}`} onClick={() => open(it)}>
                <strong>{it.cliente}</strong>
                {it.expediente_id && <span className="tag">{it.expediente_id}</span>}
                <span className="inbox-q">{it.pregunta}</span>
                <span className="muted small">{new Date(it.revision.requested_at * 1000).toLocaleString('es-ES')}</span>
              </button>
            </li>
          ))}
        </ul>
        {error && <p className="error">{error}</p>}
      </section>

      <div className="results">
        {selected ? (
          <>
            <section className="card">
              <h2>Consulta de {selected.cliente}</h2>
              <p className="pregunta">«{selected.pregunta}»</p>
              <label className="field">
                Resumen que verá el cliente (puede corregirlo)
                <textarea rows={3} value={resumen} onChange={(e) => setResumen(e.target.value)} />
              </label>
              <label className="field">
                Nota para el cliente
                <textarea rows={2} value={nota} placeholder="Opcional al aprobar; recomendable al rechazar" onChange={(e) => setNota(e.target.value)} />
              </label>
              <div className="row end">
                <button className="btn" disabled={busy} onClick={() => decide(false)}>Rechazar y atender personalmente</button>
                <button className="btn primary" disabled={busy} onClick={() => decide(true)}>Aprobar y enviar al cliente</button>
              </div>
            </section>
            <Respuesta data={selected} />
          </>
        ) : (
          <section className="card muted">Seleccione una consulta para revisar el borrador, sus fuentes y el razonamiento.</section>
        )}
      </div>
    </div>
  )
}
