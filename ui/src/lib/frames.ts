// Frames for the live preview: at most a few requests in flight, always the latest wish first, decoded bitmaps kept in a small LRU.
// (The server answers any frame in 20-140 ms and the browser's HTTP cache keeps the encoded ones, so scrubbing back and forth is instant.)

export class FramePipeline {
  private cache = new Map<number, ImageBitmap>()
  private inflight = new Set<number>()
  private queue: number[] = []
  private disposed = false
  private failures = 0

  constructor(
    private readonly fetchFrame: (n: number) => Promise<Blob>,
    private readonly onFrame: (n: number) => void,
    private readonly maxCached = 48,
    private readonly maxInflight = 3,
    private readonly decode: (b: Blob) => Promise<ImageBitmap> = (b) => createImageBitmap(b),
  ) {}

  get(n: number): ImageBitmap | undefined {
    const hit = this.cache.get(n)
    if (hit) {
      this.cache.delete(n) // refresh LRU order
      this.cache.set(n, hit)
    }
    return hit
  }

  /** The frame for `n`, or the closest earlier one within `window` frames (what playback shows while the next one loads). */
  nearest(n: number, window = 24): ImageBitmap | undefined {
    for (let d = 0; d <= window; d++) {
      const hit = this.cache.get(n - d)
      if (hit) return hit
    }
    return undefined
  }

  /** Wish for frame `n` (then `ahead` frames after it). Replaces earlier wishes: only the newest matters. */
  request(n: number, ahead = 0): void {
    if (this.disposed) return
    const wanted: number[] = []
    for (let i = 0; i <= ahead; i++) {
      const f = n + i
      if (!this.cache.has(f) && !this.inflight.has(f)) wanted.push(f)
    }
    this.queue = wanted
    this.pump()
  }

  private pump(): void {
    while (!this.disposed && this.inflight.size < this.maxInflight && this.queue.length) {
      const n = this.queue.shift() as number
      this.inflight.add(n)
      void this.fetchFrame(n)
        .then(this.decode)
        .then((bmp) => {
          if (this.disposed) return bmp.close?.()
          this.failures = 0
          this.cache.set(n, bmp)
          while (this.cache.size > this.maxCached) {
            const oldest = this.cache.keys().next().value as number
            this.cache.get(oldest)?.close?.()
            this.cache.delete(oldest)
          }
          this.onFrame(n)
        })
        .catch(() => {
          this.failures++
        })
        .finally(() => {
          this.inflight.delete(n)
          if (this.failures < 5) this.pump()
        })
    }
  }

  dispose(): void {
    this.disposed = true
    this.queue = []
    for (const b of this.cache.values()) b.close?.()
    this.cache.clear()
  }
}
