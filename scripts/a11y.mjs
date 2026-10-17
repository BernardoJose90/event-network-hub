#!/usr/bin/env node
// Accessibility audit of the running app with axe-core (WCAG 2.0/2.1/2.2 A + AA and best
// practices), in headless Chrome over the DevTools protocol. Read-only: it only views pages.
//
//   node scripts/a11y.mjs <event id> [<template id>]
//
// Env: APP_URL (default http://localhost:5173), CHROME (path to Chrome/Chromium),
// TICKET_ID (help desk ticket to open, default 1).
// Exits 1 if any page has violations. Needs Node 22+ and `npm ci` in frontend/ (axe-core).

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const [eventId, templateId] = process.argv.slice(2)
if (!eventId) {
  console.error('usage: node scripts/a11y.mjs <event id> [<template id>]')
  process.exit(2)
}
const APP = process.env.APP_URL || 'http://localhost:5173'
const CHROME = process.env.CHROME
  || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/chromium', '/usr/bin/google-chrome']
    .find((p) => fs.existsSync(p))
const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const axeSource = fs.readFileSync(path.join(root, 'frontend/node_modules/axe-core/axe.min.js'), 'utf8')
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice']

const pages = [
  ['Events', `/?doc=${eventId}`],
  ['Overview', `/event/${eventId}`],
  ['Wi-Fi networks', `/event/${eventId}?view=wifi`],
  ['Ethernet requirements', `/event/${eventId}?view=ethernet`],
  ['Partner orders', `/event/${eventId}?view=partner`],
  ['Setup', `/event/${eventId}?view=lists`],
  ['History', `/event/${eventId}?view=history`],
  ...(templateId ? [['Template', `/event/${templateId}?view=ethernet`]] : []),
  ['Help desk dashboard', '/helpdesk'],
  ['Help desk tickets', '/helpdesk?view=tickets&list=all'],
  ['Help desk ticket', `/helpdesk?view=tickets&list=all&ticket=${process.env.TICKET_ID || 1}`],
  ['Help desk reports', '/helpdesk?view=reports'],
  ['Help desk recurring issues', '/helpdesk?view=recurring'],
]

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'a11y-'))
const port = 9300 + Math.floor(Math.random() * 500)
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  '--window-size=1400,900', 'about:blank'], { stdio: 'ignore' })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let failed = 0
try {
  let targets
  for (let i = 0; i < 50 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json() } catch { await sleep(200) }
  }
  const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl)
  await new Promise((r) => ws.addEventListener('open', r))
  let id = 0
  const pending = new Map()
  ws.addEventListener('message', (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) }
  })
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })) })
  const evaluate = async (expression) => (await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })).result?.result?.value

  await send('Page.enable')
  await send('Page.navigate', { url: APP }); await sleep(800)
  // TEST ONLY identity, as chosen in the app's role switcher.
  await evaluate(`localStorage.setItem('portal-pdf-test.identity', JSON.stringify({ role: 'network-admin', user: 'a11y-check' }))`)

  for (const [name, route] of pages) {
    await send('Page.navigate', { url: APP + route }); await sleep(1800)
    await evaluate(`${axeSource};1`)
    const violations = await evaluate(`axe.run(document, { runOnly: { type: 'tag', values: ${JSON.stringify(TAGS)} } })
      .then((r) => r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target.join(' ')) })))`)
    const count = violations.reduce((t, v) => t + v.nodes.length, 0)
    failed += count
    console.log(`${count ? 'FAIL' : 'PASS'}  ${name}${count ? `: ${count} issue(s)` : ''}`)
    for (const v of violations) console.log(`      [${v.impact}] ${v.id}: ${v.help} (${v.nodes.slice(0, 3).join(', ')})`)
  }
  ws.close()
} finally {
  const exited = new Promise((r) => chrome.once('exit', r))
  chrome.kill()
  await Promise.race([exited, sleep(3000)]) // Chrome writes to its profile until it exits
  fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
}
console.log(failed ? `A11Y: FAIL (${failed} issue(s))` : 'A11Y: PASS')
process.exit(failed ? 1 : 0)
