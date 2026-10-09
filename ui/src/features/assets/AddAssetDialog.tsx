import { Info, RefreshCw, Upload } from 'lucide-react'
import { useEffect, useId, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useAssets, useCatalog, useRefreshLibrary } from '@/api/hooks'
import { ApiError } from '@/api/types'
import type { AssetDraft, AssetInfo, AssetKind } from '@/api/types'
import { Banner, Button, Chip, Dialog, Field, Input, Segmented, Spinner, toast } from '@/components/ui'
import { cn } from '@/lib/cn'
import { bytes } from '@/lib/format'
import { AssetSettings } from './AssetSettings'
import { StylePreviews } from './StylePreviews'
import { KINDS, KIND_INFO, describeError, draftLook, fieldsFromForm, formFromDraft, nameProblem, slugName, trimName, type AssetFormValue, type TimeOfDay } from './assetFields'
import { useDebounced } from './useDebounced'
import { useModalKeys } from './useModalKeys'

/** What the person already knows when they open "Add asset": a script's missing item pre-fills the form. */
export interface AddAssetInitial {
  name?: string
  kind?: AssetKind
  /** words a script uses for it (English, Hindi, Hinglish) */
  tags?: string[]
  summary?: string
  /** shown above the form: why this is being added ("your script mentions a dragon") */
  hint?: string
  /** a file already chosen (dropped on the page) */
  file?: File
}

export interface AddAssetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initial?: AddAssetInitial
  /** called once the asset is saved and in the library (the catalog and asset list are already refreshed) */
  onAdded?: (asset: AssetInfo) => void
}

const ACCEPT = '.svg,.png,.jpg,.jpeg,.webp,image/svg+xml,image/png,image/jpeg,image/webp'
const FALLBACK_STYLES = ['flat_vector', 'paper_cutout', 'stickman']
const PREVIEW_DELAY_MS = 300

interface Problem {
  detail: string
  hint?: string
}

const hasFiles = (e: DragEvent): boolean => Array.from(e.dataTransfer?.types ?? []).includes('Files')

/** A file dropped on the window anywhere else would make the browser open it in place of the app. */
function useFileDropGuard(): void {
  useEffect(() => {
    const guard = (e: globalThis.DragEvent) => {
      if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) e.preventDefault()
    }
    window.addEventListener('dragover', guard)
    window.addEventListener('drop', guard)
    return () => {
      window.removeEventListener('dragover', guard)
      window.removeEventListener('drop', guard)
    }
  }, [])
}

/**
 * Add a character, object or place to the library. Two steps: choose a file (drop it, or pick it), then check it and name it. The
 * server reads the file into a draft first; nothing is kept until "Save to library". Closing or cancelling throws the draft away.
 * It needs no open project, so any screen can open it.
 */
export function AddAssetDialog(props: AddAssetDialogProps) {
  // closed, there is nothing to keep: opening it again starts from a clean slate
  return props.open ? <AddAssetBody {...props} /> : null
}

function AddAssetBody({ onOpenChange, initial, onAdded }: AddAssetDialogProps) {
  useModalKeys()
  useFileDropGuard()
  const api = useApi()
  const uid = useId()
  const refresh = useRefreshLibrary()
  const catalog = useCatalog().data
  const library = useAssets().data
  const styles = catalog?.styles.length ? catalog.styles.map((s) => s.name) : FALLBACK_STYLES

  const [file, setFile] = useState<File | null>(initial?.file ?? null)
  const [reading, setReading] = useState(Boolean(initial?.file))
  const [readError, setReadError] = useState<Problem | null>(null)
  const [draft, setDraft] = useState<AssetDraft | null>(null)
  const [form, setForm] = useState<AssetFormValue | null>(null)
  const [trueScale, setTrueScale] = useState(false)
  const [timeOfDay, setTimeOfDay] = useState<TimeOfDay>('day')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<(Problem & { expired?: boolean }) | null>(null)
  const [conflict, setConflict] = useState<{ kind: 'builtin' | 'user'; name: string } | null>(null)
  const [over, setOver] = useState(false)
  // what the caller already knows is said before the file is chosen, and can be put right there: the file is read as that kind
  const asked = Boolean(initial?.name)
  const [wantName, setWantName] = useState(initial?.name ? slugName(initial.name) : '')
  const [wantKind, setWantKind] = useState<AssetKind | undefined>(initial?.kind)

  const alive = useRef(false)
  const request = useRef(0)
  /** the draft on the server that nobody has kept or let go of yet */
  const live = useRef<string | null>(null)
  const started = useRef<File | null>(null)
  const depth = useRef(0)
  const input = useRef<HTMLInputElement>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  const drop = () => {
    const id = live.current
    live.current = null
    if (id) void api.discardAssetDraft(id).catch(() => undefined)
  }

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      drop() // closed some other way (the screen went away): nothing is left on the server
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const read = async (f: File) => {
    const mine = ++request.current
    drop()
    setFile(f)
    setDraft(null)
    setForm(null)
    setReadError(null)
    setSaveError(null)
    setConflict(null)
    setTrueScale(false)
    setReading(true)
    try {
      const d = await api.createAssetDraft(f, { filename: f.name, kind: wantKind })
      if (!alive.current || mine !== request.current) {
        void api.discardAssetDraft(d.id).catch(() => undefined) // closed, or another file chosen, while it was being read
        return
      }
      live.current = d.id
      setDraft(d)
      setForm(formFromDraft(d, { name: trimName(wantName) || undefined, kind: wantKind, summary: initial?.summary, tags: initial?.tags }))
    } catch (e) {
      if (alive.current && mine === request.current) setReadError(describeError(e))
    } finally {
      if (alive.current && mine === request.current) setReading(false)
    }
  }

  // a file that came with the dialog (dropped on a page): straight to the second step
  useEffect(() => {
    if (initial?.file && started.current !== initial.file) {
      started.current = initial.file
      void read(initial.file)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // the second step starts on the name: that is the one thing every asset needs
  const draftId = draft?.id
  useEffect(() => {
    if (!draftId) return
    nameRef.current?.focus()
    nameRef.current?.select()
  }, [draftId])

  const pick = (files: ArrayLike<File> | null | undefined) => {
    const f = files?.[0]
    if (!f) return
    if (files && files.length > 1) toast.info('Adding the first file', 'Add one asset at a time.')
    void read(f)
  }

  const startOver = () => {
    request.current++
    drop()
    setFile(null)
    setDraft(null)
    setForm(null)
    setReadError(null)
    setSaveError(null)
    setConflict(null)
    setReading(false)
  }

  const close = () => {
    if (saving) return
    request.current++
    drop()
    onOpenChange(false)
  }

  const picture = draft?.format === 'picture'

  const save = async (replace = false) => {
    if (!draft || !form || saving || nameProblem(form.name)) return
    setSaving(true)
    setSaveError(null)
    try {
      const asset = await api.commitAssetDraft(draft.id, fieldsFromForm(form, picture), replace)
      live.current = null // the server used the draft up
      toast.success(replace ? `Replaced “${asset.name}”` : `Added “${asset.name}”`, `It is in your library now: scripts can use it as “${asset.name}”.`)
      await refresh()
      onAdded?.(asset)
      onOpenChange(false)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setConflict({ kind: e.extra.conflict === 'builtin' ? 'builtin' : 'user', name: form.name })
      else setSaveError({ ...describeError(e), expired: e instanceof ApiError && e.status === 404 })
    } finally {
      if (alive.current) setSaving(false)
    }
  }

  // -- the previews: the three styles with the settings so far, redrawn once they stop changing
  const look = draft && form ? JSON.stringify(draftLook(form, picture, trueScale, timeOfDay)) : ''
  const shown = useDebounced(look, PREVIEW_DELAY_MS, (next, now) => !next || !now) // the first picture of a file does not wait
  const srcFor = (style: string): string => (draft && shown ? api.assetDraftThumbUrl(draft.id, { style, ...JSON.parse(shown) }) : '')

  const review = Boolean(draft && form)
  const canSave = review && !saving && !nameProblem(form?.name ?? '')
  const maxMb = Math.round((library?.max_bytes ?? 12 * 1024 * 1024) / (1024 * 1024))

  const changeName = () => {
    setConflict(null)
    nameRef.current?.focus()
    nameRef.current?.select()
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && close()}
      size="xl"
      title="Add asset"
      description={review ? 'Check it and name it.' : 'Scripts can use anything you add here by its name, the same way they use the built-in ones.'}
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          {review && (
            <Button variant="primary" onClick={() => void save()} disabled={!canSave}>
              {saving && <Spinner />}
              Save to library
            </Button>
          )}
        </>
      }
    >
      <div
        className="flex flex-col gap-4"
        onDragEnter={(e) => {
          if (review || !hasFiles(e)) return
          e.preventDefault()
          depth.current++
          setOver(true)
        }}
        onDragOver={(e) => {
          if (!hasFiles(e)) return
          e.preventDefault()
          e.dataTransfer.dropEffect = review ? 'none' : 'copy'
        }}
        onDragLeave={() => {
          depth.current = Math.max(0, depth.current - 1)
          if (depth.current === 0) setOver(false)
        }}
        onDrop={(e) => {
          const dropped = e.dataTransfer?.files
          if (!hasFiles(e) && !dropped?.length) return // text dropped into a field is the field's business
          e.preventDefault()
          e.stopPropagation() // the page behind the dialog has its own drop handler
          depth.current = 0
          setOver(false)
          if (!dropped?.length) return
          if (review) toast.info('Finish this one first', 'Choose “Use another file” to switch to a different picture.')
          else if (!reading) pick(dropped)
        }}
      >
        {initial?.hint && <Banner tone="info">{initial.hint}</Banner>}

        {draft && form ? (
          <div className="grid gap-6 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
            <section aria-label="Previews" className="flex min-w-0 flex-col gap-4 md:sticky md:top-0 md:self-start">
              <StylePreviews
                styles={styles}
                srcFor={srcFor}
                subject={form.name || 'The asset'}
                kind={form.kind}
                trueScale={trueScale}
                onTrueScale={setTrueScale}
                timeOfDay={timeOfDay}
                onTimeOfDay={setTimeOfDay}
              />
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[12px] text-muted">
                <span className="min-w-0 truncate">
                  <span className="font-mono text-fg" dir="auto">
                    {draft.filename}
                  </span>{' '}
                  · {draft.format === 'svg' ? 'SVG drawing' : 'picture'} · <span className="tabular">{bytes(draft.bytes)}</span>
                </span>
                <button type="button" onClick={startOver} className="inline-flex items-center gap-1 rounded text-accent hover:underline">
                  <RefreshCw className="size-3" aria-hidden /> Use another file
                </button>
              </div>
              {draft.notes.length > 0 && (
                <section aria-labelledby={`${uid}-notes`} className="rounded-card border border-line bg-raised/50 p-3">
                  <h3 id={`${uid}-notes`} className="flex items-center gap-1.5 text-[12px] font-medium">
                    <Info className="size-3.5 text-info" aria-hidden /> How the file was read
                  </h3>
                  <ul className="m-0 mt-1.5 flex max-h-28 list-disc flex-col gap-1 overflow-y-auto pl-4 text-[12px] leading-snug text-muted">
                    {draft.notes.map((n, i) => (
                      <li key={`${i}:${n}`}>{n}</li>
                    ))}
                  </ul>
                </section>
              )}
              {draft.roles.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                  <span>You can recolour in specs:</span>
                  {draft.roles.map((r) => (
                    <Chip key={r} className="font-mono">
                      {r}
                    </Chip>
                  ))}
                </div>
              )}
            </section>

            <div className="flex min-w-0 flex-col gap-4">
              {saveError && (
                <Banner
                  tone="danger"
                  title="It could not be saved"
                  action={
                    saveError.expired ? (
                      <Button size="sm" onClick={startOver}>
                        Choose the file again
                      </Button>
                    ) : undefined
                  }
                >
                  <p className="first-letter:uppercase">{saveError.detail}</p>
                  {saveError.hint && <p className="first-letter:uppercase">{saveError.hint}</p>}
                </Banner>
              )}
              <AssetSettings
                value={form}
                onChange={(next) => {
                  if (conflict && next.name !== conflict.name) setConflict(null)
                  setForm(next)
                }}
                picture={picture}
                nameRef={nameRef}
                disabled={saving}
                onSubmit={() => void save()}
                nameError={conflict ? `There is already ${conflict.kind === 'builtin' ? 'a built-in' : 'an'} asset named “${conflict.name}”.` : null}
                nameNotice={
                  conflict && (
                    <div className="flex flex-col gap-2 rounded-ctl border border-line bg-raised p-2.5 text-[12px] leading-snug text-muted">
                      <p>
                        {conflict.kind === 'builtin'
                          ? `Replacing it makes every script that says “${conflict.name}” draw your picture instead of the built-in one.`
                          : `Replacing it swaps the picture and the settings of “${conflict.name}” for these.`}{' '}
                        Or give yours another name.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="primary" disabled={saving} onClick={() => void save(true)}>
                          Replace it with mine
                        </Button>
                        <Button size="sm" onClick={changeName}>
                          Change the name
                        </Button>
                      </div>
                    </div>
                  )
                }
              />
            </div>
          </div>
        ) : reading ? (
          <div role="status" className="grid place-items-center gap-3 rounded-card border-2 border-dashed border-line px-6 py-16 text-center">
            <Spinner className="size-6 text-accent" />
            <span className="text-[14px] font-medium">
              Reading <span className="font-mono">{file?.name}</span>…
            </span>
            <span className="text-muted">Looking at the picture and working out how big it is and what it shows.</span>
          </div>
        ) : (
          <DropZone
            over={over}
            maxMb={maxMb}
            error={readError}
            onChoose={() => input.current?.click()}
            before={
              asked && (
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
                  <Field label="Name" htmlFor={`${uid}-want-name`} hint="The id scripts use for it: lower-case letters, digits and underscores.">
                    <Input
                      id={`${uid}-want-name`}
                      value={wantName}
                      spellCheck={false}
                      autoComplete="off"
                      className="font-mono"
                      onChange={(e) => setWantName(slugName(e.target.value))}
                      onBlur={() => setWantName(trimName(wantName))}
                    />
                  </Field>
                  <Field label="Kind">
                    <Segmented<AssetKind> label="Kind" value={wantKind ?? 'object'} onChange={setWantKind} options={KINDS.map((k) => ({ value: k, label: KIND_INFO[k].label, title: KIND_INFO[k].what }))} />
                  </Field>
                </div>
              )
            }
          >
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              tabIndex={-1}
              aria-label="Choose a file"
              onChange={(e) => {
                pick(e.target.files)
                e.target.value = '' // the same file can be chosen again after an error
              }}
            />
          </DropZone>
        )}
      </div>
    </Dialog>
  )
}

function DropZone({ over, maxMb, error, onChoose, before, children }: { over: boolean; maxMb: number; error: Problem | null; onChoose: () => void; before?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      {before}
      {error && (
        <Banner tone="danger" title="This file cannot be added">
          <p className="first-letter:uppercase">{error.detail}</p>
          {error.hint && <p className="first-letter:uppercase">{error.hint}</p>}
        </Banner>
      )}
      <div className={cn('rounded-card border-2 border-dashed transition-colors', over ? 'border-accent bg-accent-soft' : 'border-line-strong hover:border-accent')}>
        <button type="button" onClick={onChoose} className="flex w-full flex-col items-center gap-2 rounded-card px-6 py-14 text-center">
          <Upload className="size-8 text-accent" strokeWidth={1.4} aria-hidden />
          <span className="text-[15px] font-semibold">Drop an SVG, PNG, JPG or WebP, or choose a file</span>
          <span className="max-w-md text-muted">
            A picture with a plain background gets it removed automatically. An SVG drawing is redrawn in all three styles and can be recoloured. Up to {maxMb} MB.
          </span>
        </button>
        {children}
      </div>
    </div>
  )
}
