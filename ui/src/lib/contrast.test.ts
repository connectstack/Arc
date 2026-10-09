// The colour tokens in index.css must stay readable: WCAG AA (4.5:1) for text in both themes, on every surface it can sit on.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const css = readFileSync(join(process.cwd(), 'src/index.css'), 'utf8') // `npm test` runs in ui/

type Tokens = Record<string, string>

function block(selector: RegExp): Tokens {
  const m = css.match(selector)
  if (!m) throw new Error(`no ${selector} block in index.css`)
  const out: Tokens = {}
  for (const [, name, value] of m[1].matchAll(/--([a-z-]+):\s*([^;]+);/g)) out[name] = value.trim()
  return out
}

const dark = block(/:root,\s*\[data-theme='dark'\]\s*\{([^}]*)\}/)
const light = { ...dark, ...block(/\[data-theme='light'\]\s*\{([^}]*)\}/) }

type RGB = [number, number, number]
function rgb(v: string): RGB {
  const hex = v.match(/^#([0-9a-f]{6})$/i)
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)) as RGB
  const fn = v.match(/^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)/)
  if (fn) return [Number(fn[1]), Number(fn[2]), Number(fn[3])]
  throw new Error(`cannot read colour ${v}`)
}
const mix = (fg: RGB, bg: RGB, a: number): RGB => fg.map((c, i) => c * a + bg[i] * (1 - a)) as RGB
function luminance([r, g, b]: RGB): number {
  const f = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const SURFACES = ['bg', 'panel', 'raised', 'hover']
const AA = 4.5

describe.each([
  ['dark', dark],
  ['light', light],
])('%s theme', (_name, t) => {
  const c = (k: string) => rgb(t[k])
  const alphaOf = (k: string) => Number(t[k].match(/\/\s*([\d.]+)/)?.[1] ?? 1)

  it.each(['text', 'muted', 'faint'])('%s text is readable on every surface and on a selected row', (k) => {
    for (const s of SURFACES) {
      expect(contrast(c(k), c(s)), `${k} on ${s}`).toBeGreaterThanOrEqual(AA)
      // a selected row is the accent at its soft alpha over the surface
      expect(contrast(c(k), mix(c('accent'), c(s), alphaOf('accent-soft'))), `${k} on a selected row over ${s}`).toBeGreaterThanOrEqual(AA)
    }
  })

  it('accent text is readable on every surface and on its own soft tint', () => {
    for (const s of SURFACES) {
      expect(contrast(c('accent'), c(s)), `accent on ${s}`).toBeGreaterThanOrEqual(AA)
      expect(contrast(c('accent'), mix(c('accent'), c(s), alphaOf('accent-soft'))), `accent on soft over ${s}`).toBeGreaterThanOrEqual(AA)
    }
  })

  it('white text on a solid accent fill (primary buttons) is readable', () => {
    expect(contrast(c('accent-fg'), c('accent-solid'))).toBeGreaterThanOrEqual(AA)
    expect(contrast(c('accent-fg'), c('accent-solid-hover'))).toBeGreaterThanOrEqual(AA)
  })

  it.each(['success', 'warning', 'danger', 'info'])('%s status text is readable on surfaces and on its chip tint', (k) => {
    for (const s of SURFACES) {
      expect(contrast(c(k), c(s)), `${k} on ${s}`).toBeGreaterThanOrEqual(AA)
      for (const a of [0.14, 0.16]) expect(contrast(c(k), mix(c(k), c(s), a)), `${k} on its ${a} tint over ${s}`).toBeGreaterThanOrEqual(AA)
    }
  })
})
