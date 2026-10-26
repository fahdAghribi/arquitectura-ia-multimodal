import { useCallback, useEffect, useRef, useState } from 'react'
import { freshToken } from './auth'

const newId = () =>
  globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`

/**
 * Conversación sobre /ws/v1/consultas/{session_id}. El canal permanece abierto: cuando el gestor
 * revisa la respuesta, el servidor la envía por aquí aunque el cliente no haya vuelto a preguntar.
 */
export function useConsultaSession() {
  const [sessionId, setSessionId] = useState(newId)
  const [trace, setTrace] = useState([])
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const wsRef = useRef(null)

  const handle = useCallback((event) => {
    const msg = JSON.parse(event.data)
    if (msg.type === 'trace') setTrace((t) => [...t, msg.step])
    if (msg.type === 'result') {
      // Un resultado tras la revisión del gestor sustituye al turno que estaba pendiente.
      setTurns((ts) => (ts.at(-1)?.status === 'pending_review' ? [...ts.slice(0, -1), msg.data] : [...ts, msg.data]))
      setBusy(false)
    }
    if (msg.type === 'error') {
      setError(msg.detail)
      setBusy(false)
    }
  }, [])

  const connect = useCallback(async () => {
    const current = wsRef.current
    if (current && current.readyState === WebSocket.OPEN) return current
    const token = await freshToken()
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    // El navegador no permite cabeceras en WebSocket: el JWT viaja como subprotocolo.
    const ws = new WebSocket(`${scheme}://${window.location.host}/ws/v1/consultas/${sessionId}`, ['asesoria.v1', `bearer.${token}`])
    await new Promise((resolve, reject) => {
      ws.onopen = resolve
      ws.onerror = () => reject(new Error('No se pudo abrir el canal de consultas'))
    })
    ws.onmessage = handle
    ws.onclose = (e) => {
      wsRef.current = null
      if (e.code >= 4000) setError(e.reason || 'Conexión rechazada')
      setBusy(false)
    }
    wsRef.current = ws
    return ws
  }, [sessionId, handle])

  const ask = useCallback(
    async ({ pregunta, expedienteId, image }) => {
      setError(null)
      setBusy(true)
      setTrace([])
      try {
        const ws = await connect()
        ws.send(JSON.stringify({ type: 'consulta', pregunta, expediente_id: expedienteId || null, image }))
      } catch (e) {
        setError(e.message)
        setBusy(false)
      }
    },
    [connect],
  )

  const reset = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
    setSessionId(newId())
    setTrace([])
    setTurns([])
    setError(null)
  }, [])

  useEffect(() => () => wsRef.current?.close(), [])

  return { sessionId, trace, turns, busy, error, ask, reset }
}
