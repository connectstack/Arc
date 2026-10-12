// A tiny Chrome DevTools Protocol driver with no dependencies (Node 22+ has WebSocket built in): it starts Chrome's headless shell,
// drives one page (navigate, evaluate, real mouse and keyboard events) and takes screenshots at any device pixel ratio.
// Used to photograph Reel Studio for the sales deck and to record the product video; nothing here is part of the product.
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const SHELL = `${process.env.HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-mac-arm64/chrome-headless-shell`
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

export async function launch({ width = 1440, height = 900, dpr = 2, port = 9333, dark = true } = {}) {
  const profile = mkdtempSync(join(tmpdir(), 'cdp-'))
  const proc = spawn(SHELL, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--hide-scrollbars', '--no-first-run', '--mute-audio', '--disable-gpu', 'about:blank'], { stdio: 'ignore' })
  let list = []
  for (let i = 0; i < 150 && !list.length; i++) {
    try {
      list = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter((t) => t.type === 'page')
    } catch {
      /* not up yet */
    }
    if (!list.length) await sleep(100)
  }
  if (!list.length) throw new Error('the headless browser did not start')
  const ws = new WebSocket(list[0].webSocketDebuggerUrl)
  await new Promise((res, rej) => ((ws.onopen = res), (ws.onerror = rej)))
  let next = 0
  const pending = new Map()
  const listeners = new Set()
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data)
    if (d.id) {
      const p = pending.get(d.id)
      pending.delete(d.id)
      if (p) d.error ? p.rej(new Error(`${p.method}: ${JSON.stringify(d.error)}`)) : p.res(d.result)
    } else listeners.forEach((f) => f(d))
  }
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      const id = ++next
      pending.set(id, { res, rej, method })
      ws.send(JSON.stringify({ id, method, params }))
    })

  await send('Page.enable')
  await send('Runtime.enable')
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: dpr, mobile: false })
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: dark ? 'dark' : 'light' }] })

  const page = {
    send,
    /** Listen to protocol events (returns a function that stops listening). */
    on(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    /** Evaluate an expression in the page (awaits promises) and return its value. */
    async eval(expression) {
      const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (r.exceptionDetails) throw new Error(`${r.exceptionDetails.text}: ${r.exceptionDetails.exception?.description ?? ''}\n${expression.slice(0, 300)}`)
      return r.result.value
    },
    async goto(url, ready) {
      let done
      const loaded = new Promise((r) => (done = r))
      const on = (d) => d.method === 'Page.loadEventFired' && done()
      listeners.add(on)
      await send('Page.navigate', { url })
      await Promise.race([loaded, sleep(15000)])
      listeners.delete(on)
      if (ready) await page.waitFor(ready)
    },
    /** Wait until a JS expression is truthy. */
    async waitFor(expression, timeout = 15000) {
      const t0 = Date.now()
      for (;;) {
        try {
          if (await page.eval(expression)) return
        } catch {
          /* the page is still loading */
        }
        if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for: ${expression.slice(0, 200)}`)
        await sleep(120)
      }
    },
    /** The box (CSS px) of the element an expression returns, or null. */
    async box(elementExpression) {
      // scroll only the containers that scroll by themselves (overflow auto or scroll): scrollIntoView would also scroll the app's own
      // overflow:hidden shell, which shifts the whole page
      return page.eval(`(() => {
        const e = (${elementExpression}); if (!e) return null
        for (let p = e.parentElement; p; p = p.parentElement) {
          if (!/(auto|scroll)/.test(getComputedStyle(p).overflowY)) continue
          const r = e.getBoundingClientRect(), pr = p.getBoundingClientRect()
          if (r.top < pr.top) p.scrollTop -= pr.top - r.top + 12
          else if (r.bottom > pr.bottom) p.scrollTop += r.bottom - pr.bottom + 12
        }
        const r = e.getBoundingClientRect()
        return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 }
      })()`)
    },
    async mouse(x, y) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
    },
    async click(x, y) {
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
    },
    /** Click the centre of the element an expression returns; returns the point clicked (CSS px). */
    async clickEl(elementExpression) {
      const b = await page.box(elementExpression)
      if (!b) throw new Error(`nothing to click: ${elementExpression.slice(0, 200)}`)
      await page.click(b.cx, b.cy)
      return { x: b.cx, y: b.cy, box: b }
    },
    async type(text, delay = 0) {
      if (!delay) return void (await send('Input.insertText', { text }))
      for (const ch of text) {
        await send('Input.insertText', { text: ch })
        await sleep(delay)
      }
    },
    async key(key, code = key, modifiers = 0) {
      const base = { key, code, modifiers, windowsVirtualKeyCode: key === 'Enter' ? 13 : key === 'Escape' ? 27 : key === 'Backspace' ? 8 : 0 }
      await send('Input.dispatchKeyEvent', { type: 'keyDown', ...base, text: key === 'Enter' ? '\r' : undefined })
      await send('Input.dispatchKeyEvent', { type: 'keyUp', ...base })
    },
    async shot(file, { clip } = {}) {
      const r = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) })
      writeFileSync(file, Buffer.from(r.data, 'base64'))
      return file
    },
    async close() {
      // the browser may close the connection without answering: never wait for the reply
      send('Browser.close').catch(() => {})
      await sleep(150)
      try {
        ws.close()
      } catch {
        /* already closed */
      }
      proc.kill()
    },
  }
  return page
}
