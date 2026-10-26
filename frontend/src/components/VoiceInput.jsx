import { useRef, useState } from 'react'

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition

/** Dictado en campo con la Web Speech API (Speech-to-Text del navegador, es-ES). */
export default function VoiceInput({ onText }) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const recRef = useRef(null)

  if (!Recognition) {
    return <button type="button" className="btn ghost" disabled title="Este navegador no soporta reconocimiento de voz">Voz no disponible</button>
  }

  function toggle() {
    if (listening) {
      recRef.current?.stop()
      return
    }
    const rec = new Recognition()
    rec.lang = 'es-ES'
    rec.interimResults = true
    rec.continuous = true
    rec.onresult = (e) => {
      let partial = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript
        if (e.results[i].isFinal) onText(text.trim())
        else partial += text
      }
      setInterim(partial)
    }
    rec.onend = () => {
      setListening(false)
      setInterim('')
    }
    rec.onerror = () => setListening(false)
    recRef.current = rec
    rec.start()
    setListening(true)
  }

  return (
    <span className="voice">
      <button type="button" className={`btn ${listening ? 'danger' : ''}`} onClick={toggle} aria-pressed={listening}>
        {listening ? '● Escuchando… (parar)' : '🎙 Dictar'}
      </button>
      {interim && <span className="muted small">{interim}</span>}
    </span>
  )
}
