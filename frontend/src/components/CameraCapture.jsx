import { useEffect, useRef, useState } from 'react'

const MAX_SIDE = 1600

/** Redimensiona y comprime en el cliente (WebP, JPEG si el navegador no codifica WebP). */
async function compress(source, width, height) {
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height)
  let blob = await new Promise((r) => canvas.toBlob(r, 'image/webp', 0.82))
  if (!blob || blob.type !== 'image/webp') blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.85))
  const data = await new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result.split(',')[1])
    reader.readAsDataURL(blob)
  })
  return { media_type: blob.type, data, preview: URL.createObjectURL(blob), size: blob.size }
}

export default function CameraCapture({ image, onChange }) {
  const videoRef = useRef(null)
  const [stream, setStream] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (videoRef.current && stream) videoRef.current.srcObject = stream
    return () => stream?.getTracks().forEach((t) => t.stop())
  }, [stream])

  async function openCamera() {
    setError(null)
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      })
      setStream(s)
    } catch (e) {
      setError(window.isSecureContext ? `Cámara no disponible: ${e.message}` : 'La cámara requiere HTTPS (o localhost).')
    }
  }

  async function capture() {
    const v = videoRef.current
    onChange(await compress(v, v.videoWidth, v.videoHeight))
    setStream(null)
  }

  async function fromFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const bitmap = await createImageBitmap(file)
    onChange(await compress(bitmap, bitmap.width, bitmap.height))
    e.target.value = ''
  }

  return (
    <div className="capture">
      {stream ? (
        <video ref={videoRef} autoPlay playsInline muted className="capture-media" />
      ) : image ? (
        <img src={image.preview} alt="Imagen adjunta" className="capture-media" />
      ) : (
        <div className="capture-placeholder">Opcional: foto de una carta de Hacienda o captura de pantalla de la sede</div>
      )}
      <div className="row">
        {stream ? (
          <button type="button" className="btn primary" onClick={capture}>Capturar</button>
        ) : (
          <button type="button" className="btn" onClick={openCamera}>Hacer foto</button>
        )}
        <label className="btn">
          Subir foto
          <input type="file" accept="image/*" capture="environment" hidden onChange={fromFile} />
        </label>
        {image && !stream && (
          <button type="button" className="btn ghost" onClick={() => onChange(null)}>Quitar</button>
        )}
        {image && <span className="muted small">{image.media_type.split('/')[1].toUpperCase()} · {Math.round(image.size / 1024)} KB</span>}
      </div>
      {error && <p className="error small">{error}</p>}
    </div>
  )
}
