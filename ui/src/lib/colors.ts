/** Scene blocks are tinted by their background template, so a reel's structure reads at a glance. */
const BG: Record<string, string> = {
  abstract: '#2dd4bf',
  forest: '#4ade80',
  rooftop: '#818cf8',
  room: '#f59e0b',
  stage: '#e879f9',
  street: '#60a5fa',
  whiteboard: '#cbd5e1',
}

export const bgColor = (template: string): string => BG[template] ?? '#94a3b8'

export function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  let h = m[1]
  if (h.length === 3) h = [...h].map((c) => c + c).join('')
  const n = parseInt(h, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

export function rgba(hex: string, alpha: number): string {
  const rgb = hexToRgb(hex)
  return rgb ? `rgb(${rgb[0]} ${rgb[1]} ${rgb[2]} / ${alpha})` : hex
}

/** Relative luminance (WCAG) of a #rrggbb colour. */
export function luminance(hex: string): number {
  const rgb = hexToRgb(hex)
  if (!rgb) return 0
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio of two #rrggbb colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

// ------------------------------------------------------------------------------- shades that follow a recoloured part
// The engine (reel/assets/art.py `role_colors`) keeps a shade of a part ("body_dark", "leaves_light") one step darker or lighter than the part
// itself when the part is recoloured and the shade is not named: the swatch the inspector shows for such a shade is worked out the same way.

/** rgb in 0..1 -> [hue, lightness, saturation] in 0..1 (Python's colorsys.rgb_to_hls). */
function rgbToHls(r: number, g: number, b: number): [number, number, number] {
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const sum = max + min
  const range = max - min
  const l = sum / 2
  if (min === max) return [0, l, 0]
  const s = l <= 0.5 ? range / sum : range / (2 - max - min)
  const rc = (max - r) / range
  const gc = (max - g) / range
  const bc = (max - b) / range
  const h = r === max ? bc - gc : g === max ? 2 + rc - bc : 4 + gc - rc
  return [(((h / 6) % 1) + 1) % 1, l, s]
}

/** [hue, lightness, saturation] -> `#rrggbb` (Python's colorsys.hls_to_rgb, lightness and saturation kept in range). */
function hlsToHex(h: number, light: number, sat: number): string {
  const l = Math.max(0, Math.min(1, light))
  const s = Math.max(0, Math.min(1, sat))
  const channel = (m1: number, m2: number, hue: number): number => {
    const x = (((hue % 1) + 1) % 1)
    if (x < 1 / 6) return m1 + (m2 - m1) * x * 6
    if (x < 0.5) return m2
    if (x < 2 / 3) return m1 + (m2 - m1) * (2 / 3 - x) * 6
    return m1
  }
  let rgb: [number, number, number]
  if (s === 0) rgb = [l, l, l]
  else {
    const m2 = l <= 0.5 ? l * (1 + s) : l + s - l * s
    const m1 = 2 * l - m2
    const hue = (((h % 1) + 1) % 1)
    rgb = [channel(m1, m2, hue + 1 / 3), channel(m1, m2, hue), channel(m1, m2, hue - 1 / 3)]
  }
  return `#${rgb.map((v) => Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, '0')).join('')}`
}

/** `newBase` stepped the way a drawing steps from `base` to its `shade`: the same change of lightness, the new hue. Null when a colour is not hex. */
export function shadeLike(base: string, shade: string, newBase: string): string | null {
  const [b, s, n] = [hexToRgb(base), hexToRgb(shade), hexToRgb(newBase)]
  if (!b || !s || !n) return null
  const [, lb, sb] = rgbToHls(b[0] / 255, b[1] / 255, b[2] / 255)
  const [, ls, ss] = rgbToHls(s[0] / 255, s[1] / 255, s[2] / 255)
  const [hn, ln, sn] = rgbToHls(n[0] / 255, n[1] / 255, n[2] / 255)
  const light = ls <= lb ? (lb > 1e-3 ? ln * (ls / lb) : ln) : ln + (1 - ln) * (lb < 1 - 1e-3 ? (ls - lb) / (1 - lb) : 0)
  const sat = sb > 1e-3 ? sn * (ss / sb) : sn
  return hlsToHex(hn, light, sat)
}

/**
 * The colour a shade ("body_dark") has when its part ("body") is recoloured and the shade itself is not: `drawing` is the drawing's own colour
 * for each part, `palette` the colours the object sets. Null when `role` is not such a shade.
 */
export function followedShade(drawing: Record<string, string>, palette: Record<string, string>, role: string): string | null {
  if (role in palette || !(role in drawing)) return null
  for (const suffix of ['_dark', '_light']) {
    const base = role.endsWith(suffix) ? role.slice(0, -suffix.length) : ''
    if (base && base in palette && base in drawing) return shadeLike(drawing[base], drawing[role], palette[base])
  }
  return null
}
