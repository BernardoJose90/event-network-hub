import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import EditorPage from './EditorPage.jsx'
import './styles.css'

// "/edit/<documentId>/<version>" is the full-screen editor page; anything else is the app.
const editMatch = window.location.pathname.match(/^\/edit\/(\d+)\/(\d+)\/?$/)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {editMatch ? <EditorPage documentId={Number(editMatch[1])} version={Number(editMatch[2])} /> : <App />}
  </StrictMode>,
)
