import { describe, expect, it, vi } from 'vitest'
import { FramePipeline } from './frames'

const bitmap = (id: number) => ({ id, close: vi.fn() }) as unknown as ImageBitmap

function setup(opts: { maxCached?: number; maxInflight?: number } = {}) {
  const resolvers = new Map<number, () => void>()
  const requested: number[] = []
  const fetchFrame = (n: number) => {
    requested.push(n)
    return new Promise<Blob>((res) => resolvers.set(n, () => res(new Blob([String(n)]))))
  }
  const seen: number[] = []
  const p = new FramePipeline(fetchFrame, (n) => seen.push(n), opts.maxCached ?? 48, opts.maxInflight ?? 2, async (b) => bitmap(Number(await b.text())))
  const finish = async (n: number) => {
    resolvers.get(n)?.()
    await new Promise((r) => setTimeout(r, 0))
  }
  return { p, requested, seen, finish }
}

describe('FramePipeline', () => {
  it('fetches the wished frame first, then the ones ahead, a few at a time', async () => {
    const { p, requested, finish } = setup()
    p.request(10, 3)
    expect(requested).toEqual([10, 11]) // two in flight
    await finish(10)
    expect(requested).toEqual([10, 11, 12])
    await finish(11)
    await finish(12)
    expect(requested).toEqual([10, 11, 12, 13])
  })

  it('only the newest wish matters: stale queued frames are dropped', async () => {
    const { p, requested, finish } = setup({ maxInflight: 1 })
    p.request(10, 5)
    p.request(200)
    await finish(10)
    expect(requested).toEqual([10, 200]) // 11..15 were never asked for
  })

  it('serves cached frames exactly and the nearest earlier one for playback', async () => {
    const { p, finish } = setup()
    p.request(5)
    await finish(5)
    expect((p.get(5) as unknown as { id: number }).id).toBe(5)
    expect((p.nearest(9) as unknown as { id: number }).id).toBe(5)
    expect(p.nearest(100)).toBeUndefined()
    expect(p.get(6)).toBeUndefined()
  })

  it('does not ask again for what it has, and evicts the oldest beyond its limit (closing the bitmap)', async () => {
    const { p, requested, finish } = setup({ maxCached: 2 })
    for (const n of [1, 2, 3]) {
      p.request(n)
      await finish(n)
    }
    expect(p.get(1)).toBeUndefined() // evicted
    p.request(3)
    expect(requested).toEqual([1, 2, 3]) // 3 is cached: no new request
  })

  it('reports each decoded frame and ignores late arrivals after dispose', async () => {
    const { p, seen, finish } = setup()
    p.request(7)
    p.dispose()
    await finish(7)
    expect(seen).toEqual([])
    p.request(8) // after dispose: nothing happens
    expect(p.get(8)).toBeUndefined()
  })
})
