// Small JS versions of the engine's easing curves, only for the little curve previews in the inspector.
const c1 = 1.70158
const c3 = c1 + 1

export function easeFn(name: string): (u: number) => number {
  switch (name) {
    case 'linear':
      return (u) => u
    case 'ease_in':
      return (u) => u * u
    case 'ease_out':
      return (u) => 1 - (1 - u) ** 2
    case 'ease_in_cubic':
      return (u) => u ** 3
    case 'ease_out_cubic':
      return (u) => 1 - (1 - u) ** 3
    case 'ease_in_out_quint':
      return (u) => (u < 0.5 ? 16 * u ** 5 : 1 - (-2 * u + 2) ** 5 / 2)
    case 'sine_in_out':
      return (u) => -(Math.cos(Math.PI * u) - 1) / 2
    case 'expo_out':
      return (u) => (u >= 1 ? 1 : 1 - 2 ** (-10 * u))
    case 'overshoot':
      return (u) => 1 + c3 * (u - 1) ** 3 + c1 * (u - 1) ** 2
    case 'anticipate':
      return (u) => c3 * u ** 3 - c1 * u ** 2
    case 'anticipate_overshoot':
      return (u) => (u < 0.5 ? (c3 * (2 * u) ** 3 - c1 * (2 * u) ** 2) / 2 : 0.5 + (1 + c3 * (2 * u - 2) ** 3 + c1 * (2 * u - 2) ** 2) / 2)
    case 'bounce':
    case 'bounce_in': {
      const out = (x: number) => {
        const n1 = 7.5625
        const d1 = 2.75
        if (x < 1 / d1) return n1 * x * x
        if (x < 2 / d1) return n1 * (x -= 1.5 / d1) * x + 0.75
        if (x < 2.5 / d1) return n1 * (x -= 2.25 / d1) * x + 0.9375
        return n1 * (x -= 2.625 / d1) * x + 0.984375
      }
      return name === 'bounce' ? out : (u) => 1 - out(1 - u)
    }
    case 'elastic':
      return (u) => (u <= 0 ? 0 : u >= 1 ? 1 : 2 ** (-10 * u) * Math.sin(((u * 10 - 0.75) * (2 * Math.PI)) / 3) + 1)
    case 'spring':
      return (u) => 1 - Math.exp(-6 * u) * Math.cos(10 * u)
    case 'hold':
      return (u) => (u >= 1 ? 1 : 0)
    default:
      return (u) => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2)
  }
}

export function curvePath(name: string, w = 44, h = 22, pad = 3): string {
  const f = easeFn(name)
  const pts: string[] = []
  for (let i = 0; i <= 24; i++) {
    const u = i / 24
    const y = Math.max(-0.3, Math.min(1.3, f(u)))
    pts.push(`${i === 0 ? 'M' : 'L'}${(pad + u * (w - 2 * pad)).toFixed(1)} ${(h - pad - y * (h - 2 * pad)).toFixed(1)}`)
  }
  return pts.join(' ')
}
