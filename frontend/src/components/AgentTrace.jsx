const ICONS = {
  guardrail: '🛡',
  privacy: '🔒',
  routing: '🧭',
  reasoning: '💭',
  action: '🔎',
  observation: '📄',
  expediente: '📁',
  review: '✍️',
  delivery: '📨',
  final: '✅',
}

/** Progreso en vivo de la consulta (el cliente ve los pasos; el gestor, además, el razonamiento). */
export default function AgentTrace({ steps, busy }) {
  if (!steps.length && !busy) return null
  return (
    <section className="card">
      <h2>Progreso</h2>
      <ol className="trace">
        {steps.map((s, i) => (
          <li key={i}>
            <span className="trace-icon" aria-hidden>{ICONS[s.kind] || '•'}</span>
            <span className="trace-node">{s.node}</span>
            <span className="trace-detail">{s.detail}</span>
          </li>
        ))}
        {busy && (
          <li className="trace-pending">
            <span className="spinner" /> trabajando…
          </li>
        )}
      </ol>
    </section>
  )
}
