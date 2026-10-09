import { ImagePlus, Plus } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useAssets, useCatalog } from '@/api/hooks'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Banner, Button, Segmented, toast } from '@/components/ui'
import { AddAssetDialog, type AddAssetInitial } from '@/features/assets/AddAssetDialog'
import { plural, titleCase } from '@/lib/format'
import { useUi } from '@/store/ui'
import { LibraryBrowser, type LibrarySection } from './LibraryBrowser'
import { SECTION_KIND, SECTION_OF_KIND, isAssetSection } from './libraryItems'

const hasFiles = (e: { dataTransfer: DataTransfer | null }): boolean => Array.from(e.dataTransfer?.types ?? []).includes('Files')

/** Everything a script can ask for, with real thumbnails drawn by the same renderer the video uses. Your own characters, objects and places are added here. */
export function LibraryPage() {
  const catalog = useCatalog().data
  const library = useAssets().data
  const fallback = useUi((s) => s.defaultStyle)
  const [style, setStyle] = useState(fallback)
  const [section, setSection] = useState<LibrarySection>('actions')
  const [adding, setAdding] = useState<AddAssetInitial | null>(null)
  const [dragging, setDragging] = useState(false)
  const [reveal, setReveal] = useState<{ section: LibrarySection; key: number }>()
  const styles = catalog?.styles.map((s) => ({ value: s.name, label: titleCase(s.name) })) ?? []

  // "Add asset" starts on the kind being looked at (the Objects tab adds an object), unless the caller knows better
  const add = (initial?: AddAssetInitial) => setAdding({ ...(isAssetSection(section) ? { kind: SECTION_KIND[section] } : {}), ...initial })

  // a picture dragged in from the desktop: show where it will go, and never let the browser open it in place of the app. While the
  // Add asset dialog is open, it takes the drop itself and the page behind it stays as it is.
  const dialogOpen = useRef(false)
  useEffect(() => {
    dialogOpen.current = Boolean(adding)
    if (adding) setDragging(false)
  }, [adding])
  useEffect(() => {
    const over = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      if (!dialogOpen.current) setDragging(true)
    }
    const leave = (e: globalThis.DragEvent) => !e.relatedTarget && setDragging(false) // leaving the window: nothing is entered
    const drop = (e: globalThis.DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      setDragging(false)
    }
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    window.addEventListener('drop', drop)
    return () => {
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
      window.removeEventListener('drop', drop)
    }
  }, [])

  const onDrop = (e: DragEvent) => {
    setDragging(false)
    const files = e.dataTransfer.files
    if (!files?.length || adding) return
    e.preventDefault()
    if (files.length > 1) toast.info('Adding the first file', 'Add one asset at a time.')
    add({ file: files[0] })
  }

  // the subtitle counts what a script can use: the engine's own and the library's (built in and yours); until the asset list has
  // arrived, the catalog's rows (which include the library's) stand in
  const assets = library?.assets
  const total = (kind: 'character' | 'object' | 'place', rows: { library?: string }[] = []) =>
    assets ? rows.filter((e) => !e.library).length + assets.filter((a) => a.kind === kind).length : rows.length
  const characters = total('character', catalog?.archetypes)
  const objects = total('object', catalog?.objects)
  const places = total('place', catalog?.backgrounds)
  const yours = (assets ?? []).filter((a) => a.origin === 'user').length
  const problems = library?.problems ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col" onDrop={onDrop}>
      <PageHeader
        title="Library"
        subtitle={
          catalog
            ? `${catalog.actions.length} actions · ${plural(characters, 'character')} · ${plural(objects, 'object')} · ${plural(places, 'place')} · ${catalog.sfx.length} sounds${yours ? ` · ${yours} yours` : ''}`
            : 'Everything a script can ask for'
        }
        actions={
          <>
            {styles.length > 0 && <Segmented label="Preview in style" size="sm" value={style} onChange={setStyle} options={styles} />}
            <Button variant="primary" onClick={() => add()}>
              <Plus className="size-4" aria-hidden /> Add asset
            </Button>
          </>
        }
      />
      <Page className="flex flex-col overflow-hidden">
        <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 flex-col px-4 pb-0 pt-4 sm:px-6">
          {problems.length > 0 && (
            <div className="mb-4">
              <Banner tone="warning" title={`${plural(problems.length, 'file')} in your assets folder could not be read`}>
                <ul className="m-0 mt-1 max-h-28 list-none overflow-y-auto p-0">
                  {problems.map((p, i) => (
                    <li key={`${i}:${p.file}`} className="break-words">
                      <code className="break-all font-mono text-[12px] text-fg">{p.file}</code>: {p.message}
                    </li>
                  ))}
                </ul>
                <p className="mt-1.5">
                  Your assets live in <code className="break-all font-mono text-[12px] text-fg">{library?.folder}</code>. Fix or remove these files and they are read again; nothing else is affected.
                </p>
              </Banner>
            </div>
          )}
          <LibraryBrowser browse styleName={style} onSectionChange={setSection} onAddAsset={add} reveal={reveal} />
        </div>
      </Page>

      {dragging && !adding && (
        <div className="fade pointer-events-none absolute inset-0 z-30 grid place-items-center bg-bg/80 backdrop-blur-[2px]">
          <div className="rounded-[16px] border-2 border-dashed border-accent px-10 py-8 text-center">
            <ImagePlus className="mx-auto mb-2 size-8 text-accent" aria-hidden />
            <div className="text-[15px] font-semibold">Drop a picture to add it to the library</div>
            <div className="mt-0.5 text-muted">SVG, PNG, JPG or WebP</div>
          </div>
        </div>
      )}

      {adding && <AddAssetDialog open onOpenChange={(o) => !o && setAdding(null)} initial={adding} onAdded={(a) => setReveal({ section: SECTION_OF_KIND[a.kind], key: Date.now() })} />}
    </div>
  )
}
