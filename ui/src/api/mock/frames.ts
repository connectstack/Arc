// A stand-in renderer for the mock adapter: flat wireframe frames drawn on a canvas, so the whole UI (scrubbing, drag handles,
// timeline) can be demonstrated without the engine. The real frames come from `reel serve`.
import type { Catalog, ReelSpec, Scene, Vec2 } from '../types'
import { sceneAt, sceneSlots } from '@/lib/timeline'

const SKY: Record<string, [string, string]> = {
  dawn: ['#f6b78a', '#ffe3c0'],
  day: ['#7fb6e6', '#d8ecfb'],
  dusk: ['#5b4b8a', '#f09a7c'],
  night: ['#0f1730', '#2a3a6a'],
}
const GROUND: Record<string, string> = {
  street: '#59616f',
  forest: '#4d7a4f',
  room: '#9b7b5a',
  rooftop: '#4a4f5c',
  stage: '#5a3b64',
  whiteboard: '#f4f4f0',
  abstract: '#6b7aa8',
}

function slotPosition(catalog: Catalog | null, scene: Scene, pos: Layer['position']): Vec2 {
  if (Array.isArray(pos)) return pos
  const bg = catalog?.backgrounds.find((b) => b.name === scene.background.template)
  return bg?.slots?.[pos] ?? [0.5, 0.74]
}
type Layer = Scene['layers'][number]

export function sceneAtTime(spec: ReelSpec, t: number): { scene: Scene; local: number; index: number } | null {
  const slots = sceneSlots(spec)
  const slot = sceneAt(slots, t)
  if (!slot) return null
  return { scene: spec.scenes[slot.index], local: Math.max(0, t - slot.start), index: slot.index }
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const out: string[] = []
  let line = ''
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word
    if (ctx.measureText(next).width > maxW && line) {
      out.push(line)
      line = word
    } else line = next
  }
  if (line) out.push(line)
  return out
}

export function drawMockFrame(ctx: CanvasRenderingContext2D, spec: ReelSpec, catalog: Catalog | null, t: number, w: number, h: number): void {
  const hit = sceneAtTime(spec, t)
  ctx.clearRect(0, 0, w, h)
  if (!hit) {
    ctx.fillStyle = '#222'
    ctx.fillRect(0, 0, w, h)
    return
  }
  const { scene, local, index } = hit
  const tod = String(scene.background.params.time_of_day ?? 'day')
  const [top, bottom] = SKY[tod] ?? SKY.day
  const g = ctx.createLinearGradient(0, 0, 0, h * 0.74)
  g.addColorStop(0, top)
  g.addColorStop(1, bottom)
  ctx.fillStyle = g
  ctx.fillRect(0, 0, w, h)
  ctx.fillStyle = GROUND[scene.background.template] ?? '#666'
  ctx.fillRect(0, h * 0.74, w, h * 0.26)
  ctx.fillStyle = 'rgba(0,0,0,.12)'
  for (let i = 0; i < 5; i++) ctx.fillRect(w * (0.08 + i * 0.2), h * (0.5 + (i % 2) * 0.05), w * 0.12, h * 0.24)

  for (const layer of scene.layers) {
    const ch = spec.characters.find((c) => c.id === layer.character)
    const [px, py] = slotPosition(catalog, scene, layer.position)
    const s = (layer.scale || 1) * (h / 960) * 1.0
    const x = px * w
    const y = py * h
    const shirt = ch?.palette.shirt ?? catalog?.archetypes.find((a) => a.name === ch?.archetype)?.palette?.shirt ?? '#e4572e'
    const skin = ch?.palette.skin ?? '#f0c29a'
    const pants = ch?.palette.pants ?? '#2d4a6b'
    const bodyH = 150 * s
    ctx.fillStyle = 'rgba(0,0,0,.2)'
    ctx.beginPath()
    ctx.ellipse(x, y, 34 * s, 8 * s, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = pants
    ctx.fillRect(x - 16 * s, y - 62 * s, 32 * s, 62 * s)
    ctx.fillStyle = shirt
    ctx.beginPath()
    ctx.roundRect(x - 24 * s, y - bodyH + 8 * s, 48 * s, 92 * s, 14 * s)
    ctx.fill()
    ctx.fillStyle = skin
    ctx.beginPath()
    ctx.arc(x, y - bodyH - 14 * s, 26 * s, 0, Math.PI * 2)
    ctx.fill()
    const active = layer.actions.find((a) => local >= a.t0 && local < a.t1)
    ctx.font = `600 ${Math.max(10, 12 * s * 1.4)}px Inter, system-ui, sans-serif`
    ctx.textAlign = 'center'
    ctx.fillStyle = 'rgba(255,255,255,.92)'
    ctx.fillText(ch?.name ?? layer.character, x, y + 22 * s * 1.2)
    if (active) {
      ctx.fillStyle = 'rgba(0,0,0,.45)'
      const label = active.name
      const tw = ctx.measureText(label).width
      ctx.beginPath()
      ctx.roundRect(x - tw / 2 - 8, y - bodyH - 78 * s, tw + 16, 22, 8)
      ctx.fill()
      ctx.fillStyle = '#fff'
      ctx.fillText(label, x, y - bodyH - 63 * s)
    }
  }

  const cap = scene.captions.find((c) => local >= c.t0 && local < c.t1)
  if (cap) {
    const size = cap.style === 'shout' ? 44 : cap.style === 'title' ? 38 : 26
    ctx.font = `800 ${size * (w / 540)}px Inter, system-ui, sans-serif`
    ctx.textAlign = 'center'
    const lines = wrap(ctx, cap.style === 'subtitle' ? cap.text : cap.text.toUpperCase(), w * 0.84)
    const baseY = cap.style === 'title' ? h * 0.2 : cap.style === 'shout' ? h * 0.3 : h * 0.73
    lines.forEach((ln, i) => {
      const yy = baseY + i * size * (w / 540) * 1.15
      ctx.lineWidth = 6
      ctx.strokeStyle = 'rgba(0,0,0,.65)'
      ctx.strokeText(ln, w / 2, yy)
      ctx.fillStyle = cap.style === 'shout' ? '#ffd34d' : '#fff'
      ctx.fillText(ln, w / 2, yy)
    })
  }
  ctx.textAlign = 'left'
  ctx.font = `500 ${11 * (w / 540)}px "JetBrains Mono", monospace`
  ctx.fillStyle = 'rgba(255,255,255,.75)'
  ctx.fillText(`mock · ${scene.id} · scene ${index + 1} · ${t.toFixed(2)}s`, 12, 18)
}

export function mockFrameBlob(spec: ReelSpec, catalog: Catalog | null, t: number, w: number, h: number): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return Promise.reject(new Error('no canvas'))
  drawMockFrame(ctx, spec, catalog, t, w, h)
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png'))
}

/** A tiny inline SVG poster (used for project cards and library tiles in the mock). */
export function posterSvg(seed: string, tod = 'day', shirt = '#2a9d8f'): string {
  const [top, bottom] = SKY[tod] ?? SKY.day
  let h = 0
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) >>> 0
  const x1 = 30 + (h % 25)
  const x2 = 60 + ((h >> 3) % 25)
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 160"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${top}"/><stop offset="1" stop-color="${bottom}"/></linearGradient></defs><rect width="90" height="160" fill="url(#g)"/><rect y="118" width="90" height="42" fill="#59616f"/><g><circle cx="${x1}" cy="86" r="8" fill="#f0c29a"/><rect x="${x1 - 9}" y="95" width="18" height="30" rx="6" fill="${shirt}"/></g><g><circle cx="${x2}" cy="92" r="7" fill="#f0c29a"/><rect x="${x2 - 8}" y="100" width="16" height="26" rx="6" fill="#e4572e"/></g></svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}
