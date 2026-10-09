import { Grid3x3, Loader2, Maximize, PanelLeft, PanelRight, Pause, Play, Repeat, ScanLine, SkipBack, SkipForward, StepBack, StepForward, Volume2, VolumeX } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import { ApiError } from '@/api/types'
import type { AudioResult, PreviewInfo, ReelSpec } from '@/api/types'
import { Banner, IconButton, Tip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { FramePipeline } from '@/lib/frames'
import { useHotkeys } from '@/lib/hotkeys'
import { movePaths } from '@/lib/paths'
import { characterColor, characterHeightFrac, characterName, entry, layerPoint } from '@/lib/spec'
import { clamp, sceneAt, sceneSlots, timecode, totalDuration } from '@/lib/timeline'
import { useWide } from '@/lib/useMedia'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { hasSound, useVoicePreview } from './useVoicePreview'

// ------------------------------------------------------------------------------- the preview session
export interface PreviewSession {
  info: PreviewInfo | null
  error: ApiError | null
  busy: boolean
}

/** Keeps a server-side preview of the current spec: re-created shortly after the last edit, the old frames staying on screen meanwhile.
 *  With `timings` (from the voice) the mouths follow the speech. */
export function usePreviewSession(spec: ReelSpec | null, timings?: AudioResult['word_timings']): PreviewSession {
  const api = useApi()
  const [state, setState] = useState<PreviewSession>({ info: null, error: null, busy: true })
  const first = useRef(true)
  useEffect(() => {
    if (!spec) return
    let cancelled = false
    const timer = setTimeout(
      () => {
        setState((s) => ({ ...s, busy: true }))
        api
          .createPreview(spec, 0.5, timings)
          .then((info) => !cancelled && setState({ info, error: null, busy: false }))
          .catch((e) => !cancelled && setState((s) => ({ info: s.info, error: e instanceof ApiError ? e : new ApiError(0, String(e)), busy: false })))
      },
      first.current ? 0 : 220,
    )
    first.current = false
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [api, spec, timings])
  return state
}

// ------------------------------------------------------------------------------- the canvas
function useFramePipeline(info: PreviewInfo | null, onFrame: (n: number, b: ImageBitmap) => void): FramePipeline | null {
  const api = useApi()
  const cb = useRef(onFrame)
  cb.current = onFrame
  const pipe = useMemo(() => {
    if (!info) return null
    const id = info.preview_id
    const p: FramePipeline = new FramePipeline(
      (n) => api.frame(id, n),
      (n) => {
        const bmp = p.get(n)
        if (bmp) cb.current(n, bmp)
      },
    )
    return p
  }, [api, info])
  useEffect(() => () => pipe?.dispose(), [pipe])
  return pipe
}

function Stage({ session, onSize }: { session: PreviewSession; onSize?: (w: number, h: number) => void }) {
  const spec = useProject((s) => s.spec) as ReelSpec
  const playhead = useProject((s) => s.playhead)
  const playing = useStudio((s) => s.playing)
  const holder = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const wantedRef = useRef(0)
  const info = session.info
  const fps = info?.fps ?? spec.meta.fps
  const frame = Math.max(0, Math.min((info?.total_frames ?? 1) - 1, Math.round(playhead * fps)))
  wantedRef.current = frame

  // fit a 9:16 stage in the available space
  useEffect(() => {
    const el = holder.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth - 32
      const h = el.clientHeight - 24
      const hh = Math.max(120, Math.min(h, w * (16 / 9)))
      setBox({ w: Math.round(hh * (9 / 16)), h: Math.round(hh) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  useEffect(() => onSize?.(box.w, box.h), [box, onSize])

  const draw = useCallback((bmp: ImageBitmap) => {
    const c = canvas.current
    if (!c) return
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.imageSmoothingEnabled = true
    ctx.imageSmoothingQuality = 'high'
    ctx.drawImage(bmp, 0, 0, c.width, c.height)
  }, [])

  const pipe = useFramePipeline(info, (n, bmp) => n === wantedRef.current && draw(bmp))

  // the canvas follows the stage size (and the device pixel ratio), keeping what is drawn
  useEffect(() => {
    const c = canvas.current
    if (!c || !box.w) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    c.width = Math.round(box.w * dpr)
    c.height = Math.round(box.h * dpr)
    const bmp = pipe?.nearest(wantedRef.current, 60)
    if (bmp) draw(bmp)
  }, [box, pipe, draw])

  useEffect(() => {
    if (!pipe) return
    pipe.request(frame, playing ? 14 : 0)
    const bmp = pipe.get(frame) ?? (playing ? pipe.nearest(frame) : undefined)
    if (bmp) draw(bmp)
  }, [pipe, frame, playing, draw])

  return (
    <div ref={holder} className="relative grid min-h-0 flex-1 place-items-center overflow-hidden" style={{ background: 'var(--stage)' }}>
      <div className="relative overflow-hidden rounded-[26px] bg-black shadow-[0_0_0_5px_var(--raised),0_24px_70px_rgb(0_0_0/0.5)]" style={{ width: box.w, height: box.h }}>
        <canvas ref={canvas} className="block size-full" aria-label="Preview of the reel at the playhead" role="img" />
        {!info && !session.error && <div className="absolute inset-0 grid place-items-center text-[12px] text-white/60">Preparing the preview…</div>}
        {session.busy && info && <span className="absolute left-3 top-3 rounded-full bg-black/55 px-2 py-0.5 text-[11px] text-white/85">updating…</span>}
        <StageOverlay box={box} />
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------- overlays and drag handles
const SLOT_SNAP_PX = 16

function StageOverlay({ box }: { box: { w: number; h: number } }) {
  const spec = useProject((s) => s.spec) as ReelSpec
  const playhead = useProject((s) => s.playhead)
  const selection = useProject((s) => s.selection)
  const select = useProject((s) => s.select)
  const edit = useProject((s) => s.edit)
  const begin = useProject((s) => s.beginGesture)
  const end = useProject((s) => s.endGesture)
  const overlays = useUi((s) => s.overlays)
  const catalog = useCatalog().data
  const [drag, setDrag] = useState<null | { layer: number; snap: string | null }>(null)
  const slots = useMemo(() => sceneSlots(spec), [spec])
  const slot = sceneAt(slots, playhead)
  const scene = slot ? spec.scenes[slot.index] : undefined
  if (!scene || !slot || !box.w) return null

  const W = box.w
  const H = box.h
  const sa = spec.meta.safe_area ?? { top: 0.1, bottom: 0.2, left: 0.07, right: 0.07 }
  const bg = entry(catalog?.backgrounds, scene.background.template)
  const slotList = Object.entries(bg?.slots ?? {}).filter(([n]) => !n.startsWith('off_'))

  const toNorm = (e: { clientX: number; clientY: number }, el: Element): [number, number] => {
    const r = el.getBoundingClientRect()
    return [clamp((e.clientX - r.left) / r.width, -0.1, 1.1), clamp((e.clientY - r.top) / r.height, 0, 1)]
  }

  const startMove = (li: number) => (e: React.PointerEvent<SVGGElement>) => {
    e.stopPropagation()
    const svg = (e.currentTarget.ownerSVGElement as SVGSVGElement) ?? e.currentTarget
    e.currentTarget.setPointerCapture(e.pointerId)
    select({ kind: 'layer', scene: slot.index, layer: li })
    begin()
    setDrag({ layer: li, snap: null })
    const move = (ev: PointerEvent) => {
      const [nx, ny] = toNorm(ev, svg)
      let snapName: string | null = null
      let best = SLOT_SNAP_PX
      for (const [name, [sx, sy]] of slotList) {
        const d = Math.hypot((sx - nx) * W, (sy - ny) * H)
        if (d < best) ((best = d), (snapName = name))
      }
      edit(
        (d) => {
          const l = d.scenes[slot.index]?.layers[li]
          if (l) l.position = snapName ?? [Math.round(nx * 1000) / 1000, Math.round(ny * 1000) / 1000]
        },
        { live: true },
      )
      setDrag({ layer: li, snap: snapName })
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      end()
      setDrag(null)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const startScale = (li: number) => (e: React.PointerEvent<SVGGElement>) => {
    e.stopPropagation()
    const layer = scene.layers[li]
    const [, fy] = layerPoint(scene, layer, catalog)
    const startScaleV = layer.scale
    const startDy = Math.max(10, (fy - characterHeightFrac(spec, catalog, scene, layer, fy)) * H)
    const feetY = fy * H
    const startDist = feetY - startDy
    e.currentTarget.setPointerCapture(e.pointerId)
    select({ kind: 'layer', scene: slot.index, layer: li })
    begin()
    const svg = e.currentTarget.ownerSVGElement as SVGSVGElement
    const move = (ev: PointerEvent) => {
      const r = svg.getBoundingClientRect()
      const py = ev.clientY - r.top
      const dist = Math.max(14, feetY - py)
      const next = clamp(startScaleV * (dist / Math.max(14, startDist)), 0.2, 4)
      edit((d) => void (d.scenes[slot.index].layers[li].scale = Math.round(next * 100) / 100), { live: true })
    }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      end()
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <svg className="absolute inset-0 size-full select-none" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
      {overlays.safe && (
        <g pointerEvents="none">
          <path
            fillRule="evenodd"
            fill="rgb(0 0 0 / 0.38)"
            d={`M0 0H${W}V${H}H0Z M${sa.left * W} ${sa.top * H}H${(1 - sa.right) * W}V${(1 - sa.bottom) * H}H${sa.left * W}Z`}
          />
          <rect x={sa.left * W} y={sa.top * H} width={(1 - sa.left - sa.right) * W} height={(1 - sa.top - sa.bottom) * H} fill="none" stroke="var(--accent)" strokeWidth="1" strokeDasharray="5 4" />
          <text x={sa.left * W + 6} y={sa.top * H + 14} fontSize="10" fill="var(--accent)">
            safe area
          </text>
        </g>
      )}
      {overlays.grid && (
        <g pointerEvents="none" stroke="rgb(255 255 255 / 0.4)" strokeWidth="0.75">
          <line x1={W / 3} y1="0" x2={W / 3} y2={H} />
          <line x1={(2 * W) / 3} y1="0" x2={(2 * W) / 3} y2={H} />
          <line x1="0" y1={H / 3} x2={W} y2={H / 3} />
          <line x1="0" y1={(2 * H) / 3} x2={W} y2={(2 * H) / 3} />
        </g>
      )}
      {overlays.handles && (
        <g>
          {drag &&
            slotList.map(([name, [sx, sy]]) => (
              <g key={name} pointerEvents="none" opacity={drag.snap === name ? 1 : 0.55}>
                <circle cx={sx * W} cy={sy * H} r={drag.snap === name ? 7 : 4} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
                <text x={sx * W} y={sy * H + 18} textAnchor="middle" fontSize="9.5" fill="#fff" stroke="rgb(0 0 0 / 0.6)" strokeWidth="2.5" paintOrder="stroke">
                  {name}
                </text>
              </g>
            ))}
          {scene.layers.flatMap((layer, li) => {
            // where a walking, running, entering, leaving or jumping character goes, while it does or while its clip is selected
            const color = characterColor(spec, catalog, layer.character)
            const picked = selection.kind === 'action' && selection.scene === slot.index && selection.layer === li ? selection.action : -1
            return movePaths(scene, li, catalog, playhead - slot.start)
              .filter((m) => m.active || m.action === picked)
              .map((m) => {
                const [x1, y1, x2, y2] = [m.from[0] * W, m.from[1] * H, m.to[0] * W, m.to[1] * H]
                const ang = Math.atan2(y2 - y1, x2 - x1)
                const tip = (a: number, r: number) => `${x2 + Math.cos(ang + a) * r},${y2 + Math.sin(ang + a) * r}`
                return (
                  <g key={`path-${li}-${m.action}`} pointerEvents="none" opacity={m.action === picked ? 1 : 0.7}>
                    <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#fff" strokeOpacity="0.55" strokeWidth="4.5" strokeDasharray="7 6" strokeLinecap="round" />
                    <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth="2.5" strokeDasharray="7 6" strokeLinecap="round" />
                    <polygon points={`${x2},${y2} ${tip(Math.PI - 0.45, 11)} ${tip(Math.PI + 0.45, 11)}`} fill={color} stroke="#fff" strokeWidth="1.25" strokeLinejoin="round" />
                  </g>
                )
              })
          })}
          {scene.layers.map((layer, li) => {
            const [fx, fy] = layerPoint(scene, layer, catalog)
            const cx = fx * W
            const cy = fy * H
            const top = cy - characterHeightFrac(spec, catalog, scene, layer, fy) * H
            const selected = selection.kind !== 'reel' && 'layer' in selection && selection.scene === slot.index && selection.layer === li
            const color = characterColor(spec, catalog, layer.character)
            return (
              <g key={li}>
                <line x1={cx} y1={cy} x2={cx} y2={top} stroke={color} strokeWidth="1" strokeDasharray="3 3" opacity={selected ? 0.9 : 0.35} pointerEvents="none" />
                <g onPointerDown={startMove(li)} style={{ cursor: 'grab' }} aria-hidden="true">
                  <circle cx={cx} cy={cy} r={14} fill="transparent" />
                  <circle cx={cx} cy={cy} r={selected ? 7 : 5.5} fill={color} stroke="#fff" strokeWidth="2" />
                  <text x={cx} y={cy + 24} textAnchor="middle" fontSize="11" fontWeight="600" fill="#fff" stroke="rgb(0 0 0 / 0.65)" strokeWidth="3" paintOrder="stroke">
                    {characterName(spec, layer.character)}
                    {typeof layer.position === 'string' ? ` · ${layer.position}` : ''}
                  </text>
                </g>
                {selected && (
                  <g onPointerDown={startScale(li)} style={{ cursor: 'ns-resize' }} aria-hidden="true">
                    <rect x={cx - 6} y={top - 6} width="12" height="12" rx="3" fill="#fff" stroke={color} strokeWidth="2" />
                  </g>
                )}
              </g>
            )
          })}
        </g>
      )}
    </svg>
  )
}

// ------------------------------------------------------------------------------- transport
function Transport({ fps, total }: { fps: number; total: number }) {
  const playhead = useProject((s) => s.playhead)
  const setPlayhead = useProject((s) => s.setPlayhead)
  const spec = useProject((s) => s.spec) as ReelSpec
  const playing = useStudio((s) => s.playing)
  const loop = useStudio((s) => s.loop)
  const set = useStudio((s) => s.set)
  const overlays = useUi((s) => s.overlays)
  const wide = useWide()
  const drawer = useStudio((s) => s.drawer)
  const leftOpen = useUi((s) => s.leftOpen)
  const rightOpen = useUi((s) => s.rightOpen)
  const setUi = useUi((s) => s.set)
  // docked panels are toggled for good; below 1280 px they are drawers that open on demand
  const leftShown = wide ? leftOpen : drawer === 'left'
  const rightShown = wide ? rightOpen : drawer === 'right'
  const toggleLeft = () => (wide ? setUi({ leftOpen: !leftOpen }) : set({ drawer: drawer === 'left' ? null : 'left' }))
  const toggleRight = () => (wide ? setUi({ rightOpen: !rightOpen }) : set({ drawer: drawer === 'right' ? null : 'right' }))
  const slots = useMemo(() => sceneSlots(spec), [spec])
  const voice = useVoicePreview()
  const [starting, setStarting] = useState(false)
  const frameDur = 1 / fps
  const seek = useCallback((t: number) => setPlayhead(clamp(t, 0, Math.max(0, total - frameDur))), [setPlayhead, total, frameDur])

  // playback: the picture follows the sound when there is sound, else the wall clock (the frames follow as fast as the server delivers them)
  useEffect(() => {
    if (!playing) return
    let raf = 0
    const t0 = performance.now()
    const base = useProject.getState().playhead
    let lastPush = 0
    const audio = voice.armed ? voice.el : null
    const tick = (now: number) => {
      let t = base + (now - t0) / 1000
      if (audio) {
        if (audio.paused || audio.readyState < 2) {
          raf = requestAnimationFrame(tick) // the sound has not started yet: wait for it
          return
        }
        t = audio.currentTime
      }
      const lo = loop?.[0] ?? 0
      const hi = loop?.[1] ?? total
      if (t >= hi) {
        if (loop) {
          useStudio.getState().set({ playing: false })
          setTimeout(() => (setPlayhead(lo), useStudio.getState().set({ playing: true })), 0)
        } else {
          setPlayhead(Math.max(0, total - frameDur))
          useStudio.getState().set({ playing: false })
        }
        return
      }
      if (now - lastPush > 30) {
        lastPush = now
        setPlayhead(t)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, loop, total, frameDur, setPlayhead, voice.armed, voice.el])

  const sceneIdx = sceneAt(slots, playhead)?.index ?? 0
  const gotoScene = (i: number) => slots[i] && seek(slots[i].start + 0.001)
  const toggle = async () => {
    if (playing) return set({ playing: false })
    if (starting || voice.preparing) return
    setStarting(true)
    try {
      await voice.ensure() // the voice is made the first time (and again when the words changed)
    } finally {
      setStarting(false)
    }
    set({ playing: true })
  }
  const busy = starting || voice.preparing
  const withSound = voice.sound && hasSound(spec)

  useHotkeys({
    space: () => void toggle(),
    left: () => seek(playhead - frameDur),
    right: () => seek(playhead + frameDur),
    'shift+left': () => seek(playhead - 1),
    'shift+right': () => seek(playhead + 1),
    up: () => gotoScene(Math.max(0, sceneIdx - 1)),
    down: () => gotoScene(Math.min(slots.length - 1, sceneIdx + 1)),
    home: () => seek(0),
    end: () => seek(total),
    '[': () => set({ loop: [playhead, loop?.[1] ?? total] }),
    ']': () => set({ loop: [loop?.[0] ?? 0, playhead] }),
  })

  return (
    <div className="flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-t border-line bg-panel px-2 sm:px-3">
      <Tip label={leftShown ? 'Hide the left panel' : 'Show the left panel'}>
        <IconButton label="Toggle left panel" active={leftShown} onClick={toggleLeft} className="mr-2">
          <PanelLeft className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Previous scene" shortcut="↑">
        <IconButton label="Previous scene" onClick={() => gotoScene(Math.max(0, sceneIdx - 1))} className="hidden sm:inline-flex">
          <SkipBack className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Back one frame" shortcut="←">
        <IconButton label="Back one frame" onClick={() => seek(playhead - frameDur)}>
          <StepBack className="size-4" />
        </IconButton>
      </Tip>
      <Tip label={busy ? 'Making the voice…' : playing ? 'Pause' : withSound ? 'Play with the voice and sound' : 'Play (no sound)'} shortcut="Space">
        <IconButton label={playing ? 'Pause' : 'Play'} variant="primary" onClick={() => void toggle()} aria-busy={busy} className="mx-0.5">
          {busy ? <Loader2 className="size-4 animate-[spin_1s_linear_infinite]" /> : playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </IconButton>
      </Tip>
      <Tip label="Forward one frame" shortcut="→">
        <IconButton label="Forward one frame" onClick={() => seek(playhead + frameDur)}>
          <StepForward className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Next scene" shortcut="↓">
        <IconButton label="Next scene" onClick={() => gotoScene(Math.min(slots.length - 1, sceneIdx + 1))} className="hidden sm:inline-flex">
          <SkipForward className="size-4" />
        </IconButton>
      </Tip>
      <Tip label={loop ? 'Clear the loop (set with [ and ])' : 'Loop between In and Out ([ and ])'}>
        <IconButton label="Loop" active={!!loop} onClick={() => set({ loop: loop ? null : [Math.max(0, playhead), Math.min(total, playhead + 4)] })} className="hidden sm:inline-flex">
          <Repeat className="size-4" />
        </IconButton>
      </Tip>
      <Tip label={voice.sound ? (voice.fresh ? 'Sound on: the voice is ready' : 'Sound on: the voice is made when you press Play') : 'Sound off: Play is silent'}>
        <IconButton label={voice.sound ? 'Turn the sound off' : 'Turn the sound on'} active={voice.sound} aria-pressed={voice.sound} onClick={voice.toggleSound}>
          {voice.sound ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
        </IconButton>
      </Tip>
      <div className="ml-2 flex shrink-0 items-baseline gap-1.5 font-mono text-[12px] tabular sm:ml-3 sm:gap-2 sm:text-[12.5px]" aria-label="Timecode">
        <span className="text-fg">{timecode(playhead)}</span>
        <span className="text-faint">/ {timecode(total)}</span>
        <span className="hidden text-faint md:inline">· f{Math.round(playhead * fps)}</span>
      </div>
      <span role="status" className="ml-3 hidden shrink-0 text-[12px] text-muted lg:inline">
        {busy ? 'Making the voice…' : ''}
      </span>
      <div className="flex-1" />
      <Tip label="Safe area (where platform UI will not cover captions)">
        <IconButton label="Safe area overlay" active={overlays.safe} onClick={() => setUi({ overlays: { ...overlays, safe: !overlays.safe } })} className="hidden md:inline-flex">
          <ScanLine className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Thirds grid">
        <IconButton label="Thirds grid" active={overlays.grid} onClick={() => setUi({ overlays: { ...overlays, grid: !overlays.grid } })} className="hidden md:inline-flex">
          <Grid3x3 className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Character handles: drag to place, corner to resize">
        <IconButton label="Character handles" active={overlays.handles} onClick={() => setUi({ overlays: { ...overlays, handles: !overlays.handles } })} className="hidden md:inline-flex">
          <Maximize className="size-4" />
        </IconButton>
      </Tip>
      <Tip label={rightShown ? 'Hide the inspector' : 'Show the inspector'}>
        <IconButton label="Toggle inspector" active={rightShown} onClick={toggleRight} className="ml-2">
          <PanelRight className="size-4" />
        </IconButton>
      </Tip>
    </div>
  )
}

export function Preview({ session }: { session: PreviewSession }) {
  const spec = useProject((s) => s.spec) as ReelSpec
  const total = useMemo(() => totalDuration(spec), [spec])
  return (
    <div className={cn('flex min-h-0 flex-1 flex-col')}>
      {session.error && (
        <div className="px-3 pt-3">
          <Banner tone="warning" title="The preview cannot be drawn yet">
            {session.error.detail}
            {session.error.hint ? ` — ${session.error.hint}` : ''} The last good frame stays on screen; see Problems.
          </Banner>
        </div>
      )}
      <Stage session={session} />
      <Transport fps={session.info?.fps ?? spec.meta.fps} total={total} />
    </div>
  )
}
