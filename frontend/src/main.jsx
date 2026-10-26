import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { registerSW } from 'virtual:pwa-register'
import { initAuth } from './auth'
import App from './App'
import './styles.css'

registerSW({ immediate: true })

const root = createRoot(document.getElementById('root'))

initAuth()
  .then(() =>
    root.render(
      <StrictMode>
        <App />
      </StrictMode>,
    ),
  )
  .catch((e) => root.render(<p className="error boot">No se pudo contactar con el proveedor de identidad: {String(e?.message || e)}</p>))
