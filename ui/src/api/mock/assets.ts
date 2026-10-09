// The asset library in memory for the mock adapter: the same calls as `reel serve` (list, upload -> draft -> keep, edit, delete,
// script coverage, filling gaps), over the mock catalog. A small stand-in for the engine; the real answers come from reel.
import type { Api } from '../client'
import type { AssetDraft, AssetFields, AssetInfo, AssetKind, AssetList, Catalog, CatalogEntry, Coverage, FillGapsResult, LibraryGap, ReelSpec } from '../types'
import { ApiError } from '../types'

const clone = <T>(v: T): T => structuredClone(v)
const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'a_$1') || 'asset'

const KIND_HEIGHT: Record<AssetKind, number> = { character: 420, object: 300, place: 1920 }
const KIND_COLOR: Record<AssetKind, string> = { character: '#e4572e', object: '#2a9d8f', place: '#4aa8e8' }

/** A tiny inline picture standing in for a rendered thumbnail. */
export function assetPoster(name: string, kind: AssetKind, color?: string): string {
  const c = color ?? KIND_COLOR[kind]
  const label = name.replace(/_/g, ' ').slice(0, 14)
  const art =
    kind === 'place'
      ? `<rect width="90" height="160" fill="#cdeefc"/><rect y="96" width="90" height="64" fill="#e0b872"/><circle cx="64" cy="34" r="12" fill="#ffe26a"/>`
      : kind === 'character'
        ? `<rect width="90" height="160" fill="#eef1f6"/><circle cx="45" cy="66" r="18" fill="${c}"/><rect x="29" y="82" width="32" height="46" rx="12" fill="${c}"/>`
        : `<rect width="90" height="160" fill="#eef1f6"/><rect x="22" y="62" width="46" height="46" rx="8" fill="${c}"/><rect x="30" y="52" width="30" height="14" rx="5" fill="${c}" opacity=".7"/>`
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 160">${art}<text x="45" y="148" font-size="9" font-family="Inter,system-ui,sans-serif" text-anchor="middle" fill="#2b3340">${label.replace(/[<&>]/g, '')}</text></svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

/** Words the mock knows are things a story shows (the engine's lexicon has hundreds): enough to show the "not in the library" flow. */
const LEXICON: { name: string; kind: AssetKind; words: string[] }[] = [
  { name: 'dragon', kind: 'character', words: ['dragon', 'ड्रैगन', 'drogon'] },
  { name: 'king', kind: 'character', words: ['king', 'राजा', 'raja'] },
  { name: 'castle', kind: 'place', words: ['castle', 'fort', 'किला', 'qila', 'महल', 'mahal'] },
  { name: 'temple', kind: 'place', words: ['temple', 'मंदिर', 'mandir'] },
  { name: 'sword', kind: 'object', words: ['sword', 'तलवार', 'talwar'] },
  { name: 'rickshaw', kind: 'object', words: ['rickshaw', 'रिक्शा', 'riksha'] },
  { name: 'diya', kind: 'object', words: ['diya', 'दीया', 'दिया'] },
  { name: 'dog', kind: 'character', words: ['dog', 'puppy', 'कुत्ता', 'कुत्ते', 'kutta'] },
  { name: 'village', kind: 'place', words: ['village', 'गाँव', 'गांव', 'gaon'] },
]

const words = (text: string) => [...text.matchAll(/[\p{L}\p{M}\p{N}]+/gu)].map((m) => ({ w: m[0].toLowerCase(), at: m.index ?? 0 }))

/** A few of reel's built-in assets (the real library has 70+ objects, 30+ characters and 7 places), so the Library has something to
 *  show. None of the words in `LEXICON` are used here: those stay "not in the library" for the script-coverage demo. */
interface Seed {
  name: string
  kind: AssetKind
  summary: string
  tags: string[]
  height: number
  anchor?: AssetInfo['anchor']
  roles?: string[]
  palette?: Record<string, string>
  aspect?: number
  place?: AssetInfo['place']
}

const SEEDS: Seed[] = [
  { name: 'car', kind: 'object', summary: 'A small red car, side view', tags: ['car', 'cars', 'taxi', 'automobile', 'vehicle', 'sedan', 'कार', 'गाड़ी', 'गाडी'], height: 300, anchor: [0.5, 0.9], roles: ['body', 'body_dark'], palette: { body: '#d64541', body_dark: '#a93226' }, aspect: 2.1 },
  { name: 'cake', kind: 'object', summary: 'A birthday cake with candles', tags: ['cake', 'birthday cake', 'केक'], height: 160, roles: ['icing', 'sponge'], palette: { icing: '#f6a5c0', sponge: '#e8b87a' }, aspect: 1.2 },
  { name: 'chair', kind: 'object', summary: 'A wooden chair, side view', tags: ['chair', 'seat', 'कुर्सी'], height: 330, roles: ['wood'], palette: { wood: '#b07a45' }, aspect: 0.8 },
  { name: 'balloon', kind: 'object', summary: 'A floating party balloon on a string', tags: ['balloon', 'balloons', 'गुब्बारा', 'गुब्बारे'], height: 220, anchor: [0.5, 0.5], roles: ['body'], palette: { body: '#e4572e' }, aspect: 0.8 },
  { name: 'tree', kind: 'object', summary: 'A leafy green tree', tags: ['tree', 'trees', 'पेड़', 'पेड'], height: 820, roles: ['leaves', 'trunk'], palette: { leaves: '#3f9b4f', trunk: '#7a5230' }, aspect: 0.9 },
  { name: 'book', kind: 'object', summary: 'A closed book', tags: ['book', 'notebook', 'किताब', 'पुस्तक'], height: 90, roles: ['cover'], palette: { cover: '#2a6fdb' }, aspect: 0.75 },
  { name: 'cat', kind: 'character', summary: 'A friendly orange cat, side view', tags: ['cat', 'kitten', 'kitty', 'बिल्ली', 'बिल्ला'], height: 250, anchor: [0.5, 0.94], roles: ['fur', 'belly'], palette: { fur: '#ee9b3b', belly: '#fff1dc' }, aspect: 1.3 },
  { name: 'cow', kind: 'character', summary: 'A brown and white cow, side view', tags: ['cow', 'cattle', 'गाय', 'गौ'], height: 330, anchor: [0.5, 0.94], roles: ['coat'], palette: { coat: '#8a5a36' }, aspect: 1.5 },
  { name: 'teacher', kind: 'character', summary: 'A teacher holding a book', tags: ['teacher', 'tutor', 'शिक्षक', 'अध्यापक', 'मास्टर'], height: 575, anchor: [0.5, 0.94], roles: ['shirt', 'pants'], palette: { shirt: '#2a9d8f', pants: '#37474f' }, aspect: 0.45 },
  { name: 'beach', kind: 'place', summary: 'A sunny beach with a palm tree, the sea and the sand', tags: ['beach', 'seaside', 'shore', 'coast', 'sea', 'ocean', 'sand', 'बीच', 'समुद्र', 'समंदर', 'तट'], height: 1920, place: { ground_y: 0.8, horizon: 0.56, perspective: 1, slots: { left: [0.27, 0.8], center: [0.5, 0.82], right: [0.73, 0.8] } } },
  { name: 'park', kind: 'place', summary: 'A green park with a path, benches and trees', tags: ['park', 'garden', 'पार्क', 'बगीचा', 'बाग'], height: 1920, place: { ground_y: 0.8, horizon: 0.5, perspective: 1, slots: { left: [0.27, 0.8], center: [0.5, 0.82], right: [0.73, 0.8], bench: [0.7, 0.78] } } },
]

/** A demo (VITE_API=mock, `?mock`) starts with the seeds above; a test starts with an empty library unless the address has `?library`. */
function wantsSeeds(): boolean {
  try {
    if (new URLSearchParams(location.search).has('library')) return true
  } catch {
    /* no location: not a browser */
  }
  return import.meta.env.MODE !== 'test'
}

/** What a dropped SVG tells the importer: the colours it marks (`data-role`) and the parts that cannot be drawn. */
async function readSvg(file: Blob): Promise<{ roles: string[]; notes: string[] }> {
  let text = ''
  try {
    text = typeof file.text === 'function' ? await file.text() : ''
  } catch {
    /* unreadable here: the real server reads it */
  }
  const roles = [...new Set([...text.matchAll(/data-role="([^"]+)"/g)].map((m) => m[1]))]
  const notes: string[] = []
  if (/<text[\s>]/i.test(text)) notes.push('skipped text: convert it to outlines in your editor')
  if (/<(filter|mask|clipPath)[\s>]/i.test(text)) notes.push('skipped filters, masks and clip paths: they cannot be drawn')
  if (/<image[\s>]/i.test(text)) notes.push('skipped an embedded picture')
  return { roles, notes }
}

export function createMockAssets(catalog: Catalog): Pick<
  Api,
  | 'listAssets'
  | 'createAssetDraft'
  | 'assetDraftThumbUrl'
  | 'commitAssetDraft'
  | 'discardAssetDraft'
  | 'updateAsset'
  | 'deleteAsset'
  | 'assetThumbUrl'
  | 'assetArtUrl'
  | 'scriptAssets'
  | 'fillGaps'
> {
  catalog.objects ??= []
  const kindOf = (list: 'objects' | 'archetypes' | 'backgrounds'): AssetKind => (list === 'objects' ? 'object' : list === 'archetypes' ? 'character' : 'place')
  const assets: AssetInfo[] = []
  for (const list of ['objects', 'archetypes', 'backgrounds'] as const) {
    for (const e of catalog[list]) {
      if (!e.library) continue
      assets.push({
        name: e.name,
        kind: kindOf(list),
        summary: e.summary,
        tags: e.tags ?? [],
        format: 'svg',
        height: e.height ?? KIND_HEIGHT[kindOf(list)],
        anchor: e.anchor ?? [0.5, 1],
        facing: 'right',
        origin: e.library,
        credit: '',
        roles: e.roles,
        editable: e.library === 'user',
        version: 'mock',
        file: `${e.name}.svg`,
      })
    }
  }
  const drafts = new Map<string, { file: Blob; filename: string; url: string; roles: string[] }>()
  const uid = () => Math.random().toString(16).slice(2, 12)
  /** what only a built-in asset's catalog row has: the drawing's own colours and its shape (keyed by name and origin, so a user's version of the name has none) */
  const extras = new Map<string, Pick<CatalogEntry, 'palette' | 'aspect'>>()

  const entryFor = (a: AssetInfo): CatalogEntry => ({
    name: a.name,
    summary: a.summary,
    tags: a.tags,
    height: a.height,
    anchor: a.anchor,
    library: a.origin,
    size: Math.round((a.height / 575) * 100) / 100,
    roles: a.roles,
    ...(a.kind === 'place' ? { slots: a.place?.slots ?? {}, ground_y: a.place?.ground_y ?? 0.8, perspective: a.place?.perspective ?? 1 } : {}),
    ...(a.kind === 'character' ? { category: 'sprite', palette: {} } : {}),
    ...(a.kind === 'place' ? { params: [] } : {}),
    ...extras.get(`${a.name}:${a.origin}`),
  })
  const target = (k: AssetKind) => (k === 'object' ? catalog.objects : k === 'character' ? catalog.archetypes : catalog.backgrounds)
  const publish = (a: AssetInfo) => {
    const list = target(a.kind)
    const i = list.findIndex((e) => e.name === a.name)
    if (i >= 0) list[i] = entryFor(a)
    else list.push(entryFor(a))
  }
  const unpublish = (a: AssetInfo) => {
    const list = target(a.kind)
    const i = list.findIndex((e) => e.name === a.name)
    if (i >= 0) list.splice(i, 1)
  }

  if (wantsSeeds()) {
    for (const seed of SEEDS) {
      const info: AssetInfo = {
        name: seed.name,
        kind: seed.kind,
        summary: seed.summary,
        tags: seed.tags,
        format: 'svg',
        height: seed.height,
        anchor: seed.anchor ?? [0.5, 1],
        facing: seed.kind === 'place' ? 'none' : 'right',
        origin: 'builtin',
        credit: 'drawn for reel, CC0',
        roles: seed.roles,
        editable: false,
        version: 'seed',
        file: `${seed.name}.svg`,
        ...(seed.place ? { place: seed.place } : {}),
      }
      extras.set(`${seed.name}:builtin`, { ...(seed.palette ? { palette: seed.palette } : {}), ...(seed.aspect ? { aspect: seed.aspect } : {}) })
      assets.push(info)
      publish(info)
    }
  }

  const api: ReturnType<typeof createMockAssets> = {
    listAssets: async (): Promise<AssetList> => ({ assets: clone(assets), problems: [], folder: '(in memory)/assets', other_folders: [], max_bytes: 12 * 1024 * 1024 }),

    createAssetDraft: async (file, opts = {}) => {
      if (file.size > 12 * 1024 * 1024) throw new ApiError(413, 'this file is larger than 12 MB', 'shrink the picture first')
      const filename = opts.filename ?? (file instanceof File ? file.name : 'asset')
      if (!/\.(svg|png|jpe?g|webp)$/i.test(filename) && !/^image\//.test(file.type)) throw new ApiError(415, 'this is not a picture or drawing Reel can read', 'use an SVG, PNG, JPG or WebP file')
      const kind = opts.kind ?? 'object'
      let name = slug(filename.replace(/\.[^.]+$/, ''))
      while (assets.some((a) => a.name === name)) name += '_2'
      const id = uid()
      drafts.set(id, { file, filename, url: URL.createObjectURL(file), roles: [] })
      const isSvg = /\.svg$/i.test(filename) || file.type === 'image/svg+xml'
      const read = isSvg ? await readSvg(file) : { roles: [], notes: ['made the plain background transparent'] }
      const held = drafts.get(id)
      if (held) held.roles = read.roles
      return {
        id,
        filename,
        format: isSvg ? 'svg' : 'picture',
        bytes: file.size,
        suggested: { name, kind, summary: '', tags: [], height: KIND_HEIGHT[kind], anchor: [0.5, 1], facing: 'right' },
        notes: read.notes,
        roles: read.roles,
        aspect: 1,
      } satisfies AssetDraft
    },
    assetDraftThumbUrl: (id) => drafts.get(id)?.url ?? assetPoster('draft', 'object'),
    commitAssetDraft: async (id, fields: AssetFields, replace = false) => {
      if (!drafts.has(id)) throw new ApiError(404, 'this upload has expired', 'choose the file again to continue')
      const have = assets.find((a) => a.name === fields.name)
      if (have && !replace) throw new ApiError(409, `there is already ${have.origin === 'builtin' ? 'a built-in' : 'an'} asset named '${fields.name}'`, 'choose another name, or replace it with yours', { conflict: have.origin })
      if (have) {
        unpublish(have)
        assets.splice(assets.indexOf(have), 1)
      }
      const a: AssetInfo = {
        name: fields.name,
        kind: fields.kind,
        summary: fields.summary || fields.name.replace(/_/g, ' '),
        tags: [...new Set([fields.name.replace(/_/g, ' '), ...fields.tags])],
        format: /\.svg$/i.test(drafts.get(id)!.filename) ? 'svg' : 'raster',
        height: fields.height ?? KIND_HEIGHT[fields.kind],
        anchor: fields.anchor ?? [0.5, 1],
        facing: fields.facing,
        origin: 'user',
        credit: fields.credit ?? '',
        roles: drafts.get(id)!.roles,
        editable: true,
        version: uid().slice(0, 6),
        file: drafts.get(id)!.filename,
        ...(fields.kind === 'place' ? { place: fields.place ?? { ground_y: 0.8, horizon: 0.45, perspective: 1, slots: {} } } : {}),
      }
      assets.push(a)
      publish(a)
      drafts.delete(id)
      return clone(a)
    },
    discardAssetDraft: async (id) => void drafts.delete(id),
    updateAsset: async (name, changes) => {
      const a = assets.find((x) => x.name === name)
      if (!a) throw new ApiError(404, `there is no asset named '${name}'`)
      if (!a.editable) throw new ApiError(403, 'this asset is not in your workspace library, so it cannot be changed here', 'built-in assets are read-only')
      if (changes.name && changes.name !== a.name && assets.some((x) => x.name === changes.name)) throw new ApiError(409, `there is already an asset named '${changes.name}'`)
      unpublish(a)
      Object.assign(a, { ...changes, tags: changes.tags ? [...new Set([(changes.name ?? a.name).replace(/_/g, ' '), ...changes.tags])] : a.tags, version: uid().slice(0, 6) })
      publish(a)
      return clone(a)
    },
    deleteAsset: async (name) => {
      const a = assets.find((x) => x.name === name)
      if (!a) throw new ApiError(404, `there is no asset named '${name}'`)
      if (!a.editable) throw new ApiError(403, 'this asset is not in your workspace library, so it cannot be deleted here', 'built-in assets cannot be deleted')
      unpublish(a)
      assets.splice(assets.indexOf(a), 1)
    },
    assetThumbUrl: (a, _style, _tod) => assetPoster(a.name, assets.find((x) => x.name === a.name)?.kind ?? 'object'),
    assetArtUrl: (name) => assetPoster(name, assets.find((x) => x.name === name)?.kind ?? 'object'),

    scriptAssets: async (script): Promise<Coverage> => {
      const toks = words(script)
      const covered = new Map<string, Coverage['covered'][number]>()
      const missing = new Map<string, Coverage['missing'][number]>()
      for (const { w, at } of toks) {
        const lib = assets.find((a) => a.name === w || a.tags.some((t) => t.toLowerCase() === w))
        if (lib) {
          const c = covered.get(lib.name) ?? { asset: lib.name, kind: lib.kind, source: lib.origin, words: [], count: 0 }
          c.count++
          if (!c.words.includes(w)) c.words.push(w)
          covered.set(lib.name, c)
          continue
        }
        const lex = LEXICON.find((l) => l.words.includes(w))
        if (!lex) continue
        const m = missing.get(lex.name) ?? { name: lex.name, kind: lex.kind, words: [], count: 0, snippet: script.slice(Math.max(0, at - 30), at + 40).replace(/\s+/g, ' ').trim(), at: [], tags: lex.words.filter((x) => x !== lex.name) }
        m.count++
        if (!m.words.includes(w)) m.words.push(w)
        if (m.at.length < 6) m.at.push([at, at + w.length])
        missing.set(lex.name, m)
      }
      return { covered: [...covered.values()].sort((a, b) => b.count - a.count), missing: [...missing.values()].sort((a, b) => b.count - a.count) }
    },
    fillGaps: async (spec: ReelSpec, only): Promise<FillGapsResult> => {
      const out = clone(spec)
      const gaps: LibraryGap[] = out.meta.library_gaps ?? []
      const filled: FillGapsResult['filled'] = []
      const pending: LibraryGap[] = []
      for (const g of gaps) {
        const asked = !only || only.some((o) => o.kind === g.kind && o.name.toLowerCase() === g.name.toLowerCase())
        const have = asked ? assets.find((a) => a.kind === g.kind && (a.name === slug(g.name) || a.tags.some((t) => t.toLowerCase() === g.name.toLowerCase()))) : undefined
        if (!have) {
          pending.push(g)
          continue
        }
        if (g.kind === 'character') out.characters.forEach((c) => c.id === g.character && (c.archetype = have.name))
        else if (g.kind === 'place') out.scenes.forEach((s) => (g.scenes.includes(s.id) || (!g.scenes.length && s.background.template === g.stand_in)) && (s.background = { template: have.name, params: {} }))
        else
          out.scenes.forEach((s) => {
            if (g.scenes.includes(s.id) && !s.objects.some((o) => o.asset === have.name))
              s.objects.push({ asset: have.name, position: [0.86, 0.8], scale: 1, depth: 'mid', layer: 'behind', facing: 'auto', rotation: 0, alpha: 1, t0: 0, t1: null, motions: [], palette: {} })
          })
        filled.push({ ...g, asset: have.name })
      }
      if (pending.length) out.meta.library_gaps = pending
      else delete out.meta.library_gaps
      return { spec: out, filled, pending, lint: { ok: true, total_sec: null, n_scenes: out.scenes.length, counts: { errors: 0, warnings: 0, infos: 0 }, missing: {}, issues: [] } }
    },
  }
  return api
}
