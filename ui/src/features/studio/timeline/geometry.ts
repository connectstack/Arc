export const GUTTER = 164 // width of the sticky label column
export const GUTTER_PHONE = 100 // ... on a phone
export const RULER_H = 28
export const CLIP_H = 26
export const ROW_PAD = 4
export const PAD_RIGHT = 160

/** Ruler ticks for a zoom (px per second): label every `major` seconds, a small tick every `minor`. */
export function rulerSteps(zoom: number): { major: number; minor: number } {
  const majors = [0.5, 1, 2, 5, 10, 15, 30, 60, 120]
  const major = majors.find((m) => m * zoom >= 72) ?? 120
  const minor = major <= 1 ? major / 5 : major <= 2 ? 0.5 : major <= 5 ? 1 : major <= 10 ? 2.5 : major / 3
  return { major, minor }
}

export function tickLabel(t: number): string {
  const m = Math.floor(t / 60)
  const s = t - m * 60
  const sec = Number.isInteger(s) ? String(s).padStart(2, '0') : s.toFixed(1).padStart(4, '0')
  return `${m}:${sec}`
}

export const MIN_ZOOM = 4
export const MAX_ZOOM = 160
