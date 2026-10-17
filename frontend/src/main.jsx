import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import EditorPage from './EditorPage.jsx'
import SheetPage from './SheetPage.jsx'
import EventPage from './event/EventPage.jsx'
import HelpdeskPage from './helpdesk/HelpdeskPage.jsx'
import ReportsPage from './reports/ReportsPage.jsx'
import RequestPage from './request/RequestPage.jsx'
import { DialogProvider } from './ui/Dialog.jsx'
import './styles.css'

// "/edit/<documentId>/<version>" is the full-screen Excel-style editor,
// "/sheet/<documentId>" the live sheet; anything else is the app.
const editMatch = window.location.pathname.match(/^\/edit\/(\d+)\/(\d+)\/?$/)
const sheetMatch = window.location.pathname.match(/^\/sheet\/(\d+)\/?$/)
const eventMatch = window.location.pathname.match(/^\/event\/(\d+)\/?$/)
const helpdeskMatch = /^\/helpdesk\/?$/.test(window.location.pathname)
const reportsMatch = /^\/reports\/?$/.test(window.location.pathname)
const requestMatch = window.location.pathname.match(/^\/request\/([A-Za-z0-9_-]{16,64})\/?$/)

let page = <App />
if (editMatch) page = <EditorPage documentId={Number(editMatch[1])} version={Number(editMatch[2])} />
else if (sheetMatch) page = <SheetPage documentId={Number(sheetMatch[1])} />
else if (eventMatch) page = <EventPage documentId={Number(eventMatch[1])} />
else if (helpdeskMatch) page = <HelpdeskPage />
else if (reportsMatch) page = <ReportsPage />
else if (requestMatch) page = <RequestPage token={requestMatch[1]} />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DialogProvider>{page}</DialogProvider>
  </StrictMode>,
)
