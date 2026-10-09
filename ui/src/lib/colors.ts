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
