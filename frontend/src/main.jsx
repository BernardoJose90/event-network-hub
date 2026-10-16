import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import EditorPage from './EditorPage.jsx'
import SheetPage from './SheetPage.jsx'
import './styles.css'

// "/edit/<documentId>/<version>" is the full-screen Excel-style editor,
// "/sheet/<documentId>" the live sheet; anything else is the app.
const editMatch = window.location.pathname.match(/^\/edit\/(\d+)\/(\d+)\/?$/)
const sheetMatch = window.location.pathname.match(/^\/sheet\/(\d+)\/?$/)

let page = <App />
if (editMatch) page = <EditorPage documentId={Number(editMatch[1])} version={Number(editMatch[2])} />
else if (sheetMatch) page = <SheetPage documentId={Number(sheetMatch[1])} />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {page}
  </StrictMode>,
)
