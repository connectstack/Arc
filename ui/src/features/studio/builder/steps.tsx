// The seven steps of the Build panel. Each one lists what the scene being built holds of its kind and offers the library's entries for
// it: a click adds one (a single undo step), and the inspector shows the new thing so it can be fine-tuned.
import { useQueryClient } from '@tanstack/react-query'
import { Play, Plus } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { useApi } from '@/api/context'
import { useAssets } from '@/api/hooks'
import type { Catalog, CatalogEntry, Layer } from '@/api/types'
import { Button, IconButton, Segmented, SelectBox, Textarea } from '@/components/ui'
import { AddAssetDialog } from '@/features/assets/AddAssetDialog'
import { Draggable } from '@/features/library/Draggable'
import { itemMatches, libraryItems, matchesQuery } from '@/features/library/libraryItems'
import { cn } from '@/lib/cn'
import { titleCase } from '@/lib/format'
import { MOD } from '@/lib/hotkeys'
import { characterName, entry } from '@/lib/spec'
import { useProject } from '@/store/project'
import { ObjectPicker } from '../ObjectPicker'
import { objectName, whenText } from '../objects'
import { addActionAfter, addCameraMove, addCaptionAfter, addCharacterToScene, addObject, addSfx, castInScene, deleteSelection, localTime, setBackground, setMusic, setTimeOfDay } from '../ops'
import { ContentRow, Contents, NextStep, SearchBox, Tile, secs, useBuild, type StepProps } from './parts'

const TIMES = ['dawn', 'day', 'dusk', 'night']

/** The playhead's time in scene `si`, to a tenth of a second: what a sound or a camera move added now would start at (it redraws ten times a second at most, not with every frame). */
const useLocalTime = (si: number): number => useProject((s) => (s.spec ? Math.round(localTime(s.spec, si, s.playhead) * 10) / 10 : 0))

const placeText = (p: Layer['position']): string => (typeof p === 'string' ? p.replace(/_/g, ' ') : `${p[0].toFixed(2)}, ${p[1].toFixed(2)}`)

// ------------------------------------------------------------------------------- 1 background
export function BackgroundStep({ spec, si }: StepProps) {
  const api = useApi()
  const { catalog, run } = useBuild(si)
  const assets = useAssets().data?.assets
  const [q, setQ] = useState('')
  const sc = spec.scenes[si]
  const tod = String(sc.background.params.time_of_day ?? 'day')
  const sets = useMemo(() => (catalog ? libraryItems(catalog, 'backgrounds', assets).filter((i) => itemMatches(i, q)) : []), [catalog, assets, q])
  const summary = entry(catalog?.backgrounds, sc.background.template)?.summary
  return (
    <div className="flex flex-col gap-3">
      <SearchBox value={q} onChange={setQ} label="Find a background" placeholder="Find a place: beach, kitchen, बाज़ार…" />
      <div role="group" aria-label="Backgrounds" className="grid max-h-[340px] grid-cols-3 gap-2 overflow-y-auto p-0.5">
        {sets.map((i) => (
          <Tile
            key={i.entry.name}
            src={api.libraryThumbUrl('background', i.entry.name, tod, spec.meta.style)}
            label={i.entry.name.replace(/_/g, ' ')}
            hint={i.entry.summary}
            selected={sc.background.template === i.entry.name}
            onClick={() =>
              run((d) => {
                setBackground(d, catalog, si, i.entry.name)
                return { kind: 'scene', scene: si }
              })
            }
          />
        ))}
      </div>
      {sets.length === 0 && <p className="text-center text-muted">No background matches “{q.trim()}”.</p>}
      <div>
        <div className="eyebrow mb-1.5 px-0.5">Time of day</div>
        <Segmented label="Time of day" size="sm" value={tod} onChange={(v) => run((d) => void setTimeOfDay(d, si, v))} options={TIMES.map((t) => ({ value: t, label: titleCase(t) }))} className="w-full [&>button]:flex-1 [&>button]:px-1" />
      </div>
      {summary && <p className="line-clamp-3 text-[11.5px] leading-snug text-faint">{summary}</p>}
      <NextStep to="cast" label="Characters" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 2 characters
export function CastStep({ spec, si }: StepProps) {
  const api = useApi()
  const { catalog, run } = useBuild(si)
  const assets = useAssets().data?.assets
  const [q, setQ] = useState('')
  const sc = spec.scenes[si]
  const here = new Set(sc.layers.map((l) => l.character))
  const elsewhere = spec.characters.filter((c) => !here.has(c.id))
  const items = useMemo(() => (catalog ? libraryItems(catalog, 'characters', assets).filter((i) => itemMatches(i, q)) : []), [catalog, assets, q])
  const bodies = items.filter((i) => i.origin === 'engine')
  const pictures = items.filter((i) => i.origin !== 'engine')
  const grid = (list: typeof items) => (
    <div className="grid grid-cols-3 gap-2">
      {list.map((i) => (
        <Tile key={i.entry.name} src={api.libraryThumbUrl('archetype', i.entry.name, 'day', spec.meta.style)} label={i.entry.name.replace(/_/g, ' ')} hint={i.entry.summary} onClick={() => run((d) => addCharacterToScene(d, si, i.entry.name))} />
      ))}
    </div>
  )
  return (
    <div className="flex flex-col gap-3">
      <Contents title={`In scene ${si + 1}`} empty="Nobody yet. Choose a character below.">
        {sc.layers.map((l, li) => {
          const who = spec.characters.find((c) => c.id === l.character)
          const name = characterName(spec, l.character)
          return (
            <ContentRow
              key={`${l.character}#${li}`}
              sel={{ kind: 'layer', scene: si, layer: li }}
              title={name}
              detail={`${who?.archetype ?? 'unknown'} · ${placeText(l.position)} · ${l.actions.length} ${l.actions.length === 1 ? 'action' : 'actions'}`}
              leading={who && <img src={api.libraryThumbUrl('archetype', who.archetype, 'day', spec.meta.style)} alt="" loading="lazy" className="h-8 w-6 shrink-0 rounded-[5px] bg-hover object-cover" />}
              removeLabel={`Take ${name} out of this scene`}
              onRemove={() => run((d) => deleteSelection(d, { kind: 'layer', scene: si, layer: li }))}
            />
          )
        })}
      </Contents>
      {elsewhere.length > 0 && (
        <div>
          <div className="eyebrow mb-1.5 px-0.5">Already in your reel</div>
          <div className="flex flex-wrap gap-1.5">
            {elsewhere.map((c) => (
              <button key={c.id} type="button" onClick={() => run((d) => castInScene(d, si, c.id))} aria-label={`Put ${characterName(spec, c.id)} in this scene`} className="inline-flex items-center gap-1 rounded-chip border border-line px-2 py-1 text-[12px] text-muted hover:border-line-strong hover:text-fg">
                <Plus className="size-3" aria-hidden /> {characterName(spec, c.id)}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="eyebrow px-0.5">New character</div>
      <SearchBox value={q} onChange={setQ} label="Find a character" placeholder="Find a character: cat, robot, बिल्ली…" />
      <div role="group" aria-label="Characters" className="flex max-h-[340px] flex-col gap-3 overflow-y-auto p-0.5">
        {bodies.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="text-[11.5px] text-faint">Body types · dress them any colour</div>
            {grid(bodies)}
          </div>
        )}
        {pictures.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="text-[11.5px] text-faint">Pictures from the library</div>
            {grid(pictures)}
          </div>
        )}
        {items.length === 0 && <p className="text-center text-muted">No character matches “{q.trim()}”.</p>}
      </div>
      <NextStep to="objects" label="Objects" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 3 objects
export function ObjectsStep({ spec, si }: StepProps) {
  const { catalog, run } = useBuild(si)
  const qc = useQueryClient()
  const sc = spec.scenes[si]
  // adding a drawing of your own takes a while (a dialog): it goes to the scene it was asked for, however long that takes
  const [ownOpen, setOwnOpen] = useState(false)
  const add = (name: string) =>
    // a drawing just added is in the catalog the query holds now, not in the one this render was made with
    run((d) => addObject(d, qc.getQueryData<Catalog>(['catalog']) ?? catalog, name, useProject.getState().playhead, si))
  return (
    <div className="flex flex-col gap-3">
      <Contents title={`In scene ${si + 1}`} empty="No objects yet. Find a car, a tree, a cake … below.">
        {sc.objects.map((o, oi) => (
          <ContentRow key={oi} sel={{ kind: 'object', scene: si, object: oi }} title={objectName(o.asset)} detail={whenText(sc, o)} removeLabel={`Take ${objectName(o.asset)} out of this scene`} onRemove={() => run((d) => deleteSelection(d, { kind: 'object', scene: si, object: oi }))} />
        ))}
      </Contents>
      <ObjectPicker className="w-full" onPick={add} onAddOwn={() => setOwnOpen(true)} />
      <AddAssetDialog
        open={ownOpen}
        onOpenChange={setOwnOpen}
        initial={{ kind: 'object', hint: `The drawing you add is put in scene ${si + 1}.` }}
        onAdded={(asset) => {
          setOwnOpen(false)
          add(asset.name)
        }}
      />
      <NextStep to="actions" label="Actions" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 4 actions
export function ActionsStep({ spec, si }: StepProps) {
  const { catalog, run } = useBuild(si)
  const selection = useProject((s) => s.selection)
  const [q, setQ] = useState('')
  const [pick, setPick] = useState<string>()
  const sc = spec.scenes[si]
  const people = sc.layers.map((l) => l.character)
  const picked = 'layer' in selection && selection.scene === si ? sc.layers[selection.layer]?.character : undefined
  const who = [pick, picked].find((id) => id && people.includes(id)) ?? people[0]
  const li = sc.layers.findIndex((l) => l.character === who)
  const clips = li >= 0 ? sc.layers[li].actions : []
  const groups = useMemo(() => {
    const by: Record<string, CatalogEntry[]> = {}
    for (const a of (catalog?.actions ?? []).filter((x) => matchesQuery(x, q))) (by[a.category ?? 'other'] ??= []).push(a)
    return Object.entries(by)
  }, [catalog, q])
  if (!who)
    return (
      <div className="flex flex-col gap-2">
        <p className="leading-snug text-muted">Nobody is in this scene yet: cast a character first, then give them something to do.</p>
        <NextStep to="cast" label="Characters" />
      </div>
    )
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="eyebrow mb-1.5 px-0.5">Who</div>
        <div role="group" aria-label="Who does it" className="flex flex-wrap gap-1.5">
          {people.map((id) => (
            <button key={id} type="button" aria-pressed={id === who} onClick={() => setPick(id)} className={cn('rounded-chip border px-2 py-1 text-[12px] transition-colors', id === who ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-fg')}>
              {characterName(spec, id)}
            </button>
          ))}
        </div>
      </div>
      <Contents title={`${characterName(spec, who)} in scene ${si + 1}`} empty="Nothing yet. Each action you add follows the one before it.">
        {clips.map((a, ai) => (
          <ContentRow key={ai} sel={{ kind: 'action', scene: si, layer: li, action: ai }} title={a.name} detail={`${secs(a.t0)} to ${secs(a.t1)}`} removeLabel={`Take ${a.name} out`} onRemove={() => run((d) => deleteSelection(d, { kind: 'action', scene: si, layer: li, action: ai }))} />
        ))}
      </Contents>
      <SearchBox value={q} onChange={setQ} label="Find an action" placeholder="Find an action: walk, wave, dance…" />
      <div role="group" aria-label="Actions" className="flex max-h-[340px] flex-col gap-2.5 overflow-y-auto">
        {groups.map(([category, list]) => (
          <div key={category}>
            <div className="eyebrow mb-1 px-1">{titleCase(category)}</div>
            {list.map((a) => (
              <Draggable key={a.name} item={{ kind: 'action', name: a.name }} className="rounded-ctl hover:bg-hover">
                <button type="button" onClick={() => run((d) => addActionAfter(d, catalog, si, who, a.name))} aria-label={`Add ${a.name} for ${characterName(spec, who)}`} className="flex w-full items-start gap-2 rounded-ctl px-2 py-1.5 text-left">
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{a.name}</span>
                    <span className="line-clamp-2 text-[11.5px] leading-snug text-muted">{a.summary}</span>
                  </span>
                  <Plus className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden />
                </button>
              </Draggable>
            ))}
          </div>
        ))}
        {groups.length === 0 && <p className="text-center text-muted">No action matches “{q.trim()}”.</p>}
      </div>
      <NextStep to="sounds" label="Sounds" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 5 sounds
export function SoundsStep({ spec, si }: StepProps) {
  const api = useApi()
  const { catalog, run } = useBuild(si)
  const at = useLocalTime(si)
  const [q, setQ] = useState('')
  const sc = spec.scenes[si]
  const sounds = (catalog?.sfx ?? []).filter((x) => matchesQuery(x, q))
  const play = (url: string) => {
    try {
      void Promise.resolve(new Audio(url).play()).catch(() => undefined)
    } catch {
      /* a browser without audio just stays quiet */
    }
  }
  const listen = (name: string) => play(api.sfxUrl(name))
  const music = spec.audio.music
  const moods = catalog?.music_moods ?? []
  const mood = music?.startsWith('procedural:') ? music.slice('procedural:'.length) : ''
  // a file, or a mood this engine does not know: shown as it is, to be changed in Voice & Audio
  const bed = music === null ? 'none' : music === 'procedural' ? 'auto' : moods.includes(mood) ? mood : 'custom'
  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="eyebrow mb-1.5 px-0.5">Music · the whole reel</div>
        <div className="flex items-center gap-1.5">
          <SelectBox
            label="Music"
            value={bed}
            onChange={(v) => run((d) => void setMusic(d, v))}
            className="flex-1"
            options={[
              { value: 'none', label: 'No music' },
              { value: 'auto', label: 'Chosen by the look', hint: 'a bed that suits it' },
              ...moods.map((m) => ({ value: m, label: titleCase(m) })),
              ...(bed === 'custom' ? [{ value: 'custom', label: 'Custom music', hint: 'change it in Voice & Audio', disabled: true }] : []),
            ]}
          />
          <IconButton label="Listen to the music" disabled={!moods.includes(bed)} onClick={() => play(api.musicUrl(bed, 10))}>
            <Play className="size-3.5" />
          </IconButton>
        </div>
      </div>
      <Contents title={`In scene ${si + 1}`} empty="No sounds yet. Listen with ▶, add with the name.">
        {sc.sfx.map((x, xi) => (
          <ContentRow key={xi} sel={{ kind: 'sfx', scene: si, sfx: xi }} title={x.name} detail={`at ${secs(x.t)}`} removeLabel={`Take ${x.name} out`} onRemove={() => run((d) => deleteSelection(d, { kind: 'sfx', scene: si, sfx: xi }))} />
        ))}
      </Contents>
      <SearchBox value={q} onChange={setQ} label="Find a sound" placeholder="Find a sound: pop, whoosh, applause…" />
      <p className="px-0.5 text-[11.5px] leading-snug text-faint">
        A sound plays at <b className="font-medium text-fg tabular">{secs(at)}</b> into the scene: move the playhead on the timeline to put it elsewhere.
      </p>
      <div role="group" aria-label="Sounds" className="grid max-h-[300px] grid-cols-1 gap-0.5 overflow-y-auto">
        {sounds.map((s) => (
          <Draggable key={s.name} item={{ kind: 'sfx', name: s.name }} className="group flex items-center rounded-ctl hover:bg-hover">
            <button type="button" onClick={() => run((d) => addSfx(d, s.name, useProject.getState().playhead, si))} aria-label={`Add ${s.name}`} className="flex min-w-0 flex-1 items-center gap-2 rounded-ctl px-2 py-1.5 text-left">
              <Plus className="size-3.5 shrink-0 text-faint" aria-hidden />
              <span className="truncate" title={s.summary}>
                {s.name}
              </span>
            </button>
            <button type="button" aria-label={`Listen to ${s.name}`} onClick={() => listen(s.name)} className="rounded-ctl px-2 py-1.5 text-faint hover:text-fg">
              <Play className="size-3.5" />
            </button>
          </Draggable>
        ))}
        {sounds.length === 0 && <p className="py-3 text-center text-muted">No sound matches “{q.trim()}”.</p>}
      </div>
      <NextStep to="words" label="Words" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 6 words
export function WordsStep({ spec, si }: StepProps) {
  const { catalog, run } = useBuild(si)
  const [text, setText] = useState('')
  const [speaker, setSpeaker] = useState('narration')
  const field = useRef<HTMLTextAreaElement>(null)
  const sc = spec.scenes[si]
  const add = () => {
    if (!text.trim()) return
    run((d) => addCaptionAfter(d, catalog, si, text, speaker === 'narration' ? null : speaker))
    setText('')
    field.current?.focus()
  }
  return (
    <div className="flex flex-col gap-3">
      <Contents title={`In scene ${si + 1}`} empty="No words yet. Type a line below.">
        {sc.captions.map((c, ci) => (
          <ContentRow
            key={ci}
            sel={{ kind: 'caption', scene: si, caption: ci }}
            title={c.text}
            detail={`${secs(c.t0)} to ${secs(c.t1)}${c.speaker ? ` · ${characterName(spec, c.speaker)}` : ''}`}
            removeLabel={`Take the line “${c.text.slice(0, 24)}” out`}
            onRemove={() => run((d) => deleteSelection(d, { kind: 'caption', scene: si, caption: ci }))}
          />
        ))}
      </Contents>
      <Textarea
        ref={field}
        value={text}
        rows={2}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            add()
          }
        }}
        aria-label="What is said"
        placeholder="Type a line: what is said or shown…"
        className="min-h-14"
      />
      <SelectBox
        label="Who says it"
        value={speaker}
        onChange={setSpeaker}
        options={[{ value: 'narration', label: 'Narration', hint: 'nobody in particular' }, ...spec.characters.map((c) => ({ value: c.id, label: characterName(spec, c.id) }))]}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] text-faint">{MOD}↵ adds the line after the last one.</span>
        <Button variant="primary" size="sm" disabled={!text.trim()} onClick={add}>
          <Plus className="size-3.5" aria-hidden /> Add line
        </Button>
      </div>
      <NextStep to="camera" label="Camera" />
    </div>
  )
}

// ------------------------------------------------------------------------------- 7 camera
export function CameraStep({ spec, si }: StepProps) {
  const { catalog, run } = useBuild(si)
  const at = useLocalTime(si)
  const sc = spec.scenes[si]
  return (
    <div className="flex flex-col gap-3">
      <Contents title={`In scene ${si + 1}`} empty="A still camera. Add a pan, a zoom or a shake below.">
        {sc.camera.moves.map((m, mi) => (
          <ContentRow key={mi} sel={{ kind: 'camera', scene: si, move: mi }} title={titleCase(m.type)} detail={`${secs(m.t0)} to ${secs(m.t1)}`} removeLabel={`Take the ${m.type} out`} onRemove={() => run((d) => deleteSelection(d, { kind: 'camera', scene: si, move: mi }))} />
        ))}
      </Contents>
      <p className="px-0.5 text-[11.5px] leading-snug text-faint">
        A move starts at <b className="font-medium text-fg tabular">{secs(at)}</b> into the scene: move the playhead on the timeline to start it elsewhere.
      </p>
      <div role="group" aria-label="Camera moves" className="flex flex-col gap-0.5">
        {(catalog?.camera_moves ?? []).map((m) => (
          <Draggable key={m.name} item={{ kind: 'camera', name: m.name }} className="rounded-ctl hover:bg-hover">
            <button type="button" onClick={() => run((d) => addCameraMove(d, m.name, useProject.getState().playhead, si))} aria-label={`Add a ${m.name}`} className="flex w-full items-start gap-2 rounded-ctl px-2 py-1.5 text-left">
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{titleCase(m.name)}</span>
                <span className="line-clamp-2 text-[11.5px] leading-snug text-muted">{m.summary}</span>
              </span>
              <Plus className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden />
            </button>
          </Draggable>
        ))}
      </div>
    </div>
  )
}
