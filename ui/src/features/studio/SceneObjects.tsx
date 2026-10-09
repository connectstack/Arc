// The scene outline's objects: what stands in the scene on show (the selected scene's, else the one under the playhead), each with
// select, duplicate and delete, and the control that adds one from the asset library.
import { useQueryClient } from '@tanstack/react-query'
import { Copy, Plus, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { useCatalog } from '@/api/hooks'
import type { Catalog, ReelSpec } from '@/api/types'
import { Button, IconButton } from '@/components/ui'
import { AddAssetDialog } from '@/features/assets/AddAssetDialog'
import { AssetThumb } from '@/features/assets/AssetThumb'
import { cn } from '@/lib/cn'
import { objectPersons, type Selection } from '@/lib/spec'
import { sceneAt, sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { ObjectPickerPopover, sizeText, useAssetRef } from './ObjectPicker'
import { objectName, positionText, whenText } from './objects'
import { addObject, deleteSelection, duplicateSelection } from './ops'

/** The scene the outline is about: the one the selection belongs to, else the one the playhead is in. */
export function outlineScene(spec: ReelSpec, selection: Selection, playhead: number): number {
  if ('scene' in selection && spec.scenes[selection.scene]) return selection.scene
  return sceneAt(sceneSlots(spec), playhead)?.index ?? 0
}

export function SceneObjects({ spec }: { spec: ReelSpec }) {
  const catalog = useCatalog().data
  const qc = useQueryClient()
  const ref = useAssetRef()
  const edit = useProject((s) => s.edit)
  const select = useProject((s) => s.select)
  const selection = useProject((s) => s.selection)
  const playhead = useProject((s) => s.playhead)
  // adding a drawing of your own takes a while (a dialog): the scene it was asked for is kept, however the dialog closes
  const [ownOpen, setOwnOpen] = useState(false)
  const ownFor = useRef(0)
  const si = useMemo(() => outlineScene(spec, selection, playhead), [spec, selection, playhead])
  const sc = spec.scenes[si]
  if (!sc) return null
  const objects = sc.objects ?? []

  const addTo = (name: string, scene: number) =>
    edit((d) => {
      // a drawing just added is in the catalog the query holds now, not in the one this render was made with
      const added = addObject(d, qc.getQueryData<Catalog>(['catalog']) ?? catalog, name, useProject.getState().playhead, scene)
      if (added) select(added)
    })
  const picked = (oi: number): boolean => (selection.kind === 'object' || selection.kind === 'motion') && selection.scene === si && selection.object === oi

  return (
    <section aria-label={`Objects in scene ${si + 1}`} className="flex shrink-0 flex-col border-t border-line">
      <div className="flex items-center justify-between gap-2 px-2.5 pb-1 pt-2.5">
        <h2 className="eyebrow min-w-0 truncate">Objects · scene {si + 1}</h2>
        <ObjectPickerPopover
          align="end"
          onPick={(name) => addTo(name, si)}
          onAddOwn={() => {
            ownFor.current = si
            setOwnOpen(true)
          }}
          trigger={
            <Button size="sm" variant="secondary">
              <Plus className="size-3.5" aria-hidden /> Add object
            </Button>
          }
        />
      </div>
      {objects.length === 0 ? (
        <p className="px-3 pb-3 pt-1 text-[12px] leading-snug text-faint">No objects in this scene yet. Add a car, a tree, a cake … from the library, or a picture of your own.</p>
      ) : (
        <ul className="m-0 flex max-h-[min(224px,30vh)] flex-col gap-1.5 overflow-y-auto p-2.5 pt-1.5">
          {objects.map((o, oi) => {
            const on = picked(oi)
            const name = objectName(o.asset)
            const persons = objectPersons(catalog, o)
            return (
              <li key={oi} className="list-none">
                <div className={cn('flex items-center gap-1.5 rounded-card border p-1.5 transition-colors', on ? 'border-accent bg-accent-soft' : 'border-line bg-raised hover:border-line-strong')}>
                  <button type="button" aria-pressed={on} onClick={() => select({ kind: 'object', scene: si, object: oi })} className="flex min-w-0 flex-1 items-center gap-2.5 rounded-ctl text-left">
                    <span className="block w-7 shrink-0">
                      <AssetThumb asset={ref(o.asset)} style={spec.meta.style} className="rounded-[5px]" alt="" />
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-[12.5px] font-semibold">{name}</span>{' '}
                      <span className="block truncate text-[11.5px] text-muted">
                        {[persons === undefined ? '' : sizeText(persons), positionText(o), whenText(sc, o)].filter(Boolean).join(' · ')}
                        {o.motions.length > 0 && ` · ${o.motions.length} ${o.motions.length === 1 ? 'motion' : 'motions'}`}
                      </span>
                    </span>
                  </button>
                  <IconButton label={`Duplicate ${name}`} size="icon-sm" onClick={() => edit((d) => select(duplicateSelection(d, { kind: 'object', scene: si, object: oi })))}>
                    <Copy className="size-3.5" />
                  </IconButton>
                  <IconButton label={`Delete ${name}`} size="icon-sm" className="hover:text-danger" onClick={() => edit((d) => select(deleteSelection(d, { kind: 'object', scene: si, object: oi })))}>
                    <Trash2 className="size-3.5" />
                  </IconButton>
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <AddAssetDialog
        open={ownOpen}
        onOpenChange={setOwnOpen}
        initial={{ kind: 'object', hint: `The drawing you add is put in scene ${ownFor.current + 1}.` }}
        onAdded={(asset) => {
          setOwnOpen(false)
          addTo(asset.name, ownFor.current)
        }}
      />
    </section>
  )
}
