export function bytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

export function seconds(s: number, digits = 1): string {
  if (s >= 60) {
    const m = Math.floor(s / 60)
    return `${m}m ${(s - m * 60).toFixed(0)}s`
  }
  return `${s.toFixed(digits)} s`
}

export function mbps(bps: number): string {
  return `${(bps / 1e6).toFixed(1)} Mbit/s`
}

export function ago(epochSec: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - epochSec)
  if (d < 45) return 'just now'
  if (d < 3600) return `${Math.round(d / 60)} min ago`
  if (d < 86400) return `${Math.round(d / 3600)} h ago`
  if (d < 86400 * 14) return `${Math.round(d / 86400)} d ago`
  return new Date(epochSec * 1000).toLocaleDateString()
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function words(text: string): number {
  return (text.match(/[\p{L}\p{N}']+/gu) ?? []).length
}

/** "Mr. Pip" -> "mr-pip" */
export function slug(text: string, fallback = 'item'): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || fallback
}

export function titleCase(name: string): string {
  return name.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
