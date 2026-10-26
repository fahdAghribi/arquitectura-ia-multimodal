function Lista({ titulo, items, ordered = false }) {
  if (!items?.length) return null
  const Tag = ordered ? 'ol' : 'ul'
  return (
    <>
      <h3>{titulo}</h3>
      <Tag className="lista">
        {items.map((x, i) => (
          <li key={i}>{x}</li>
        ))}
      </Tag>
    </>
  )
}

/** Respuesta estructurada: resumen, detalle, modelos, requisitos, documentación, pasos, fuentes y capturas. */
export default function Respuesta({ data }) {
  if (!data) return null
  const r = data.respuesta

  if (data.status === 'blocked') {
    return (
      <section className="card warn-card">
        <h2>No se ha podido tramitar la consulta</h2>
        <p>{data.blocked_reason}</p>
      </section>
    )
  }
  if (data.status === 'pending_review' && !r) {
    return (
      <section className="card pending-card">
        <h2>En revisión por su gestor</h2>
        <p>Hemos preparado una respuesta y su gestor la está revisando. La verá aquí en cuanto la apruebe; no hace falta que recargue la página.</p>
      </section>
    )
  }
  if (data.status === 'rejected') {
    return (
      <section className="card pending-card">
        <h2>Su gestor se encargará personalmente</h2>
        <p>{data.mensaje_gestor}</p>
      </section>
    )
  }
  if (!r) return null

  return (
    <section className="card">
      <div className="row between">
        <h2>Respuesta</h2>
        <span className="row">
          {data.status === 'pending_review' && <span className="badge pending">Borrador pendiente de revisión</span>}
          {r.requiere_asesor && <span className="badge warn">Conviene estudiarlo con su gestor</span>}
        </span>
      </div>
      {r.nota_gestor && (
        <div className="banner ok">
          <strong>Nota de su gestor:</strong> {r.nota_gestor}
        </div>
      )}
      <p className="resumen">{r.resumen}</p>
      {r.modelos.length > 0 && (
        <p className="row">
          {r.modelos.map((m) => (
            <span key={m} className="tag strong">{m}</span>
          ))}
        </p>
      )}
      {r.respuesta_detallada && (
        <details open={r.respuesta_detallada.length < 900}>
          <summary>Explicación detallada</summary>
          <div className="detalle">{r.respuesta_detallada}</div>
        </details>
      )}
      <Lista titulo="Requisitos" items={r.requisitos} />
      <Lista titulo="Documentación necesaria" items={r.documentacion} />
      <Lista titulo="Pasos a seguir" items={r.pasos} ordered />
      <Lista titulo="Plazos" items={r.plazos} />
      <Lista titulo="Necesitamos saber" items={r.preguntas_aclaratorias} />

      {r.capturas.length > 0 && (
        <>
          <h3>Capturas de las guías</h3>
          <div className="figures">
            {r.capturas.map((c) => (
              <figure key={c.url}>
                <a href={c.url} target="_blank" rel="noreferrer">
                  <img src={c.url} alt={c.descripcion || c.image_name} loading="lazy" />
                </a>
                <figcaption>
                  {c.descripcion}
                  <span className="muted small"> — {c.documento}, pág. {c.pagina}</span>
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}

      {r.advertencias.length > 0 && (
        <ul className="advertencias">
          {r.advertencias.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}

      {r.fuentes.length > 0 && (
        <details>
          <summary>Fuentes consultadas ({r.fuentes.length})</summary>
          <ul className="sources">
            {r.fuentes.map((f) => (
              <li key={f.chunk_id}>
                <strong>{f.documento}</strong>, pág. {f.pagina}
                {f.seccion && <span className="muted"> · {f.seccion}</span>}
                {!f.vigente && <span className="tag warn"> ejercicio antiguo</span>}
                <pre>{f.extracto}</pre>
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="muted small">
        Confianza {Math.round(r.confianza * 100)} % · {data.agent} · {data.model_used || 'n/d'}
      </p>
    </section>
  )
}
