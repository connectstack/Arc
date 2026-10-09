// Spec files may leave out everything that has a default (the engine fills it in): `normalizeSpec` fills those in for editing,
// `stripDefaults` takes them out again before saving, so a project file stays as small and readable as one written by hand.
import type { ActionClip, Caption, CameraMove, Character, Layer, ObjectMotion, ReelSpec, Scene, SceneObject } from '@/api/types'

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const arr = <T>(v: unknown, map: (x: unknown, i: number) => T): T[] => (Array.isArray(v) ? v.map((x, i) => map(x, i)) : [])
const num = (v: unknown, d: number): number => (typeof v === 'number' && Number.isFinite(v) ? v : d)
const str = (v: unknown, d: string): string => (typeof v === 'string' ? v : d)

const DEFAULT_POSITION = [0.5, 0.8]

function action(a: unknown): ActionClip {
  const o = isObj(a) ? a : {}
  return { ...o, name: str(o.name, 'idle'), t0: num(o.t0, 0), t1: num(o.t1, 1), params: isObj(o.params) ? o.params : {} } as ActionClip
}

function layer(l: unknown): Layer {
  const o = isObj(l) ? l : {}
  const pos = o.position
  return {
    ...o,
    character: str(o.character, ''),
    position: typeof pos === 'string' || (Array.isArray(pos) && pos.length === 2) ? (pos as Layer['position']) : [...DEFAULT_POSITION] as [number, number],
    scale: num(o.scale, 1),
    depth: (['background', 'mid', 'foreground'].includes(str(o.depth, '')) ? o.depth : 'mid') as Layer['depth'],
    facing: (['auto', 'left', 'right'].includes(str(o.facing, '')) ? o.facing : 'auto') as Layer['facing'],
    actions: arr(o.actions, action),
  }
}

function objectMotion(m: unknown): ObjectMotion {
  const o = isObj(m) ? m : {}
  return { ...o, type: str(o.type, 'float'), t0: num(o.t0, 0), t1: num(o.t1, 1), ease: str(o.ease, 'ease_in_out') } as ObjectMotion
}

function sceneObject(x: unknown): SceneObject {
  const o = isObj(x) ? x : {}
  const pos = o.position
  const t1 = o.t1
  return {
    ...o,
    asset: str(o.asset, ''),
    position: typeof pos === 'string' || (Array.isArray(pos) && pos.length === 2) ? (pos as SceneObject['position']) : ([...DEFAULT_POSITION] as [number, number]),
    scale: num(o.scale, 1),
    depth: (['background', 'mid', 'foreground'].includes(str(o.depth, '')) ? o.depth : 'mid') as SceneObject['depth'],
    layer: (['behind', 'front'].includes(str(o.layer, '')) ? o.layer : 'behind') as SceneObject['layer'],
    facing: (['auto', 'left', 'right'].includes(str(o.facing, '')) ? o.facing : 'auto') as SceneObject['facing'],
    rotation: num(o.rotation, 0),
    alpha: num(o.alpha, 1),
    t0: num(o.t0, 0),
    t1: typeof t1 === 'number' && Number.isFinite(t1) ? t1 : null,
    motions: arr(o.motions, objectMotion),
    palette: isObj(o.palette) ? (o.palette as Record<string, string>) : {},
  }
}

function caption(c: unknown): Caption {
  const o = isObj(c) ? c : {}
  return {
    ...o,
    text: str(o.text, ''),
    t0: num(o.t0, 0),
    t1: num(o.t1, 1),
    style: str(o.style, 'subtitle'),
    anchor: (['auto', 'top', 'center', 'bottom'].includes(str(o.anchor, '')) ? o.anchor : 'auto') as Caption['anchor'],
  } as Caption
}

function move(m: unknown): CameraMove {
  const o = isObj(m) ? m : {}
  return { ...o, type: str(o.type, 'pan'), t0: num(o.t0, 0), t1: num(o.t1, 1), ease: str(o.ease, 'ease_in_out'), params: isObj(o.params) ? o.params : {} } as CameraMove
}

function scene(s: unknown, i: number): Scene {
  const o = isObj(s) ? s : {}
  const bg = isObj(o.background) ? o.background : {}
  const cam = isObj(o.camera) ? o.camera : {}
  const tr = isObj(o.transition_out) ? o.transition_out : {}
  return {
    ...o,
    id: str(o.id, `scene_${i + 1}`),
    duration_sec: num(o.duration_sec, 5),
    background: { ...bg, template: str(bg.template, 'abstract'), params: isObj(bg.params) ? bg.params : {} },
    camera: { ...cam, moves: arr(cam.moves, move) },
    layers: arr(o.layers, layer),
    objects: arr(o.objects, sceneObject),
    captions: arr(o.captions, caption),
    sfx: arr(o.sfx, (x) => {
      const q = isObj(x) ? x : {}
      return { ...q, name: str(q.name, 'pop'), t: num(q.t, 0), volume: num(q.volume, 1) }
    }),
    transition_out: { ...tr, type: str(tr.type, 'cut'), duration: num(tr.duration, 0), params: isObj(tr.params) ? tr.params : {} },
  } as Scene
}

function character(c: unknown, i: number): Character {
  const o = isObj(c) ? c : {}
  return { ...o, id: str(o.id, `actor_${i + 1}`), archetype: str(o.archetype, 'everyman'), props: arr(o.props, (p) => String(p)), palette: isObj(o.palette) ? (o.palette as Character['palette']) : {} } as Character
}

export function normalizeSpec(raw: unknown): ReelSpec {
  const o = isObj(raw) ? raw : {}
  const meta = isObj(o.meta) ? o.meta : {}
  const audio = isObj(o.audio) ? o.audio : {}
  // "nothing is missing from the library" is written as no list at all (so what is saved and what is edited stay the same)
  const { library_gaps: gaps, ...rest } = meta
  return {
    ...o,
    version: str(o.version, '1.0'),
    meta: {
      ...rest,
      ...(Array.isArray(gaps) && gaps.length > 0 ? { library_gaps: gaps } : {}),
      title: str(meta.title, 'Untitled'),
      style: str(meta.style, 'paper_cutout'),
      fps: num(meta.fps, 30),
      resolution: (Array.isArray(meta.resolution) && meta.resolution.length === 2 ? meta.resolution : [1080, 1920]) as [number, number],
      seed: num(meta.seed, 0),
      target_duration_sec: num(meta.target_duration_sec, 50),
      aspect: '9:16',
    },
    characters: arr(o.characters, character),
    scenes: arr(o.scenes, scene),
    audio: {
      ...audio,
      music: audio.music === undefined ? null : (audio.music as string | null),
      voiceover: (['tts', 'file', 'none'].includes(str(audio.voiceover, '')) ? audio.voiceover : 'none') as ReelSpec['audio']['voiceover'],
      ducking: typeof audio.ducking === 'boolean' ? audio.ducking : true,
      music_gain_db: num(audio.music_gain_db, -16),
      voice_gain_db: num(audio.voice_gain_db, 0),
      sfx_gain_db: num(audio.sfx_gain_db, -8),
      auto_sfx: typeof audio.auto_sfx === 'boolean' ? audio.auto_sfx : false,
    },
  } as ReelSpec
}

const emptyObj = (v: unknown): boolean => isObj(v) && Object.keys(v).length === 0
const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)

/** Remove what the engine would fill in anyway. The result is what gets written to the project file. */
export function stripDefaults(spec: ReelSpec): Obj {
  const out = structuredClone(spec) as unknown as Obj
  const drop = (o: Obj, key: string, test: (v: unknown) => boolean) => {
    if (key in o && test(o[key])) delete o[key]
  }
  const meta = out.meta as Obj
  drop(meta, 'fps', (v) => v === 30)
  drop(meta, 'resolution', (v) => same(v, [1080, 1920]))
  drop(meta, 'seed', (v) => v === 0)
  drop(meta, 'target_duration_sec', (v) => v === 50)
  drop(meta, 'aspect', () => true)
  for (const k of Object.keys(meta)) if (meta[k] === null) delete meta[k]
  drop(meta, 'library_gaps', (v) => Array.isArray(v) && v.length === 0)
  for (const c of out.characters as Obj[]) {
    drop(c, 'props', (v) => Array.isArray(v) && v.length === 0)
    drop(c, 'palette', emptyObj)
    drop(c, 'name', (v) => v === null || v === '')
    drop(c, 'voice', (v) => v === null || v === '')
  }
  for (const s of out.scenes as Obj[]) {
    const bg = s.background as Obj
    drop(bg, 'params', emptyObj)
    const cam = s.camera as Obj
    for (const m of cam.moves as Obj[]) {
      drop(m, 'ease', (v) => v === 'ease_in_out')
      drop(m, 'params', emptyObj)
      drop(m, 'from', (v) => v === null)
      drop(m, 'to', (v) => v === null)
    }
    drop(s, 'camera', (v) => (v as Obj).moves instanceof Array && ((v as Obj).moves as unknown[]).length === 0)
    for (const l of s.layers as Obj[]) {
      drop(l, 'scale', (v) => v === 1)
      drop(l, 'depth', (v) => v === 'mid')
      drop(l, 'facing', (v) => v === 'auto')
      drop(l, 'position', (v) => same(v, DEFAULT_POSITION))
      for (const a of l.actions as Obj[]) drop(a, 'params', emptyObj)
      drop(l, 'actions', (v) => Array.isArray(v) && v.length === 0)
    }
    drop(s, 'layers', (v) => Array.isArray(v) && v.length === 0)
    for (const ob of (s.objects ?? []) as Obj[]) {
      drop(ob, 'scale', (v) => v === 1)
      drop(ob, 'depth', (v) => v === 'mid')
      drop(ob, 'layer', (v) => v === 'behind')
      drop(ob, 'facing', (v) => v === 'auto')
      drop(ob, 'rotation', (v) => v === 0)
      drop(ob, 'alpha', (v) => v === 1)
      drop(ob, 't0', (v) => v === 0)
      drop(ob, 't1', (v) => v === null)
      drop(ob, 'position', (v) => same(v, DEFAULT_POSITION))
      drop(ob, 'palette', emptyObj)
      for (const m of (ob.motions ?? []) as Obj[]) {
        drop(m, 'ease', (v) => v === 'ease_in_out')
        for (const k of ['from', 'to', 'amount', 'count']) drop(m, k, (v) => v === null)
      }
      drop(ob, 'motions', (v) => Array.isArray(v) && v.length === 0)
    }
    drop(s, 'objects', (v) => Array.isArray(v) && v.length === 0)
    for (const c of s.captions as Obj[]) {
      drop(c, 'style', (v) => v === 'subtitle')
      drop(c, 'anchor', (v) => v === 'auto')
      drop(c, 'speaker', (v) => v === null || v === '')
      drop(c, 'speak', (v) => v === null)
    }
    drop(s, 'captions', (v) => Array.isArray(v) && v.length === 0)
    for (const x of s.sfx as Obj[]) drop(x, 'volume', (v) => v === 1)
    drop(s, 'sfx', (v) => Array.isArray(v) && v.length === 0)
    const tr = s.transition_out as Obj
    drop(tr, 'params', emptyObj)
    drop(s, 'transition_out', (v) => (v as Obj).type === 'cut' && !(v as Obj).duration)
    drop(s, 'notes', (v) => v === null || v === '')
  }
  const audio = out.audio as Obj
  drop(audio, 'ducking', (v) => v === true)
  drop(audio, 'music_gain_db', (v) => v === -16)
  drop(audio, 'voice_gain_db', (v) => v === 0)
  drop(audio, 'sfx_gain_db', (v) => v === -8)
  drop(audio, 'auto_sfx', (v) => v === false)
  drop(audio, 'voiceover_file', (v) => v === null || v === '')
  drop(audio, 'tts_voice', (v) => v === null || v === '')
  return out
}
