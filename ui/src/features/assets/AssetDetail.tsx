import { Copy, Download, Info, Pencil, Trash2 } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { useApi } from '@/api/context'
import { useCatalog, useRefreshLibrary } from '@/api/hooks'
import { ApiError } from '@/api/types'
import type { AssetInfo, Catalog, CatalogEntry } from '@/api/types'
import { Banner, Button, Chip, Dialog, Spinner, toast } from '@/components/ui'
import { titleCase } from '@/lib/format'
import { AssetSettings } from './AssetSettings'
import { StylePreviews } from './StylePreviews'
import { FACINGS, KIND_INFO, anchorText, changesFromForm, describeError, formFromAsset, sizeText, specSnippet, type AssetFormValue, type TimeOfDay } from './assetFields'
import { useModalKeys } from './useModalKeys'

const FALLBACK_STYLES = ['flat_vector', 'paper_cutout', 'stickman']

export interface AssetDetailProps {
  asset: AssetInfo
  open: boolean
  onOpenChange: (open: boolean) => void
  /** an edit was saved: the asset as it is now (it may have another name) */
  onUpdated?: (asset: AssetInfo) => void
  /** the asset was deleted (the library is already refreshed) */
  onDeleted?: (asset: AssetInfo) => void
  /** a built-in asset: the person wants to replace it with a picture of their own (opens "Add asset" with the same name) */
  onOverride?: (asset: AssetInfo) => void
}

/** The catalog row that carries an asset's drawing colours. */
function entryOf(catalog: Catalog | undefined, asset: AssetInfo): CatalogEntry | undefined {
  const list = asset.kind === 'object' ? catalog?.objects : asset.kind === 'character' ? catalog?.archetypes : catalog?.backgrounds
  return list?.find((e) => e.name === asset.name)
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[12.5px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="m-0 min-w-0 break-words text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="flex flex-col gap-1.5">
      <h3 id={id} className="eyebrow">
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * One asset of the library, closely: the three styles side by side (with the true-size switch), its facts, the colours a spec can
 * change and the words that find it. Your own assets can be edited, downloaded and deleted here; the built-in ones are read-only
 * (and say how to put a version of your own in their place).
 */
export function AssetDetail({ asset, open, onOpenChange, onUpdated, onDeleted, onOverride }: AssetDetailProps) {
  return open ? <AssetDetailBody asset={asset} open={open} onOpenChange={onOpenChange} onUpdated={onUpdated} onDeleted={onDeleted} onOverride={onOverride} /> : null
}

function AssetDetailBody({ asset, onOpenChange, onUpdated, onDeleted, onOverride }: AssetDetailProps) {
  useModalKeys()
  const api = useApi()
  const refresh = useRefreshLibrary()
  const catalog = useCatalog().data
  const styles = catalog?.styles.length ? catalog.styles.map((s) => s.name) : FALLBACK_STYLES
  const picture = asset.format === 'raster'
  const place = asset.kind === 'place'
  const pretty = titleCase(asset.name)

  const [trueScale, setTrueScale] = useState(false)
  const [timeOfDay, setTimeOfDay] = useState<TimeOfDay>('day')
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<AssetFormValue>(() => formFromAsset(asset))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<{ detail: string; hint?: string } | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<{ detail: string; hint?: string } | null>(null)

  // keyboard: editing starts on the name, and ending it puts you back on the Edit button
  const nameRef = useRef<HTMLInputElement>(null)
  const editRef = useRef<HTMLButtonElement>(null)
  const wasEditing = useRef(false)
  useEffect(() => {
    if (editing) nameRef.current?.focus()
    else if (wasEditing.current) editRef.current?.focus()
    wasEditing.current = editing
  }, [editing])

  const before = useMemo(() => formFromAsset(asset), [asset])
  const row = entryOf(catalog, asset)
  const roles = asset.roles ?? row?.roles ?? []
  const snippet = specSnippet(asset)
  const canCopy = typeof navigator !== 'undefined' && typeof navigator.clipboard?.writeText === 'function'

  const startEdit = () => {
    setForm(formFromAsset(asset))
    setError(null)
    setNameError(null)
    setEditing(true)
  }

  const save = async () => {
    if (saving) return
    const changes = changesFromForm(before, form, picture)
    if (Object.keys(changes).length === 0) {
      setEditing(false)
      return
    }
    setSaving(true)
    setError(null)
    setNameError(null)
    try {
      const updated = await api.updateAsset(asset.name, changes)
      await refresh()
      toast.success(`Saved “${updated.name}”`)
      onUpdated?.(updated)
      setEditing(false)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNameError(e.detail)
      else setError(describeError(e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async () => {
    if (deleting) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await api.deleteAsset(asset.name)
      await refresh()
      toast.success(`Deleted “${asset.name}”`)
      setConfirming(false)
      onDeleted?.(asset)
      onOpenChange(false)
    } catch (e) {
      setDeleteError(describeError(e))
    } finally {
      setDeleting(false)
    }
  }

  const askToDelete = () => {
    setDeleteError(null)
    setConfirming(true)
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(snippet.json)
      toast.success('Copied', 'Paste it into your spec.')
    } catch {
      toast.error('Could not copy', 'Select the text and copy it by hand.')
    }
  }

  const origin = asset.origin === 'builtin' ? 'Built in' : asset.editable ? 'Yours' : 'From another folder'
  const facts: [string, ReactNode][] = [
    ['Kind', KIND_INFO[asset.kind].label],
    ['Size', place ? 'The whole frame' : `${sizeText(asset)} (${Math.round(asset.height)} px tall)`],
  ]
  if (!place) {
    facts.push(['Anchor', anchorText(asset.anchor)])
    facts.push(['Facing', FACINGS.find((f) => f.value === asset.facing)?.label ?? asset.facing])
  }
  const background = asset.cutout === true ? ', plain background removed' : asset.cutout === false ? ', background kept' : ', plain background removed when it has no transparency'
  facts.push(['Format', picture ? `Picture${background}` : 'SVG drawing'])
  facts.push(['File', <span className="font-mono">{asset.file}</span>])
  if (asset.credit) facts.push(['Credit', <span dir="auto">{asset.credit}</span>])
  if (place && asset.place) {
    facts.push(['Ground line', `${Math.round(asset.place.ground_y * 100)}% down the frame`])
    facts.push(['Horizon', `${Math.round(asset.place.horizon * 100)}% down the frame`])
    facts.push(['Perspective', String(asset.place.perspective)])
    facts.push(['Standing spots', Object.keys(asset.place.slots).join(', ') || 'left, center, right'])
  }

  const footer = editing ? (
    <>
      <Button onClick={() => setEditing(false)} disabled={saving}>
        Cancel
      </Button>
      <Button variant="primary" onClick={() => void save()} disabled={saving}>
        {saving && <Spinner />}
        Save changes
      </Button>
    </>
  ) : asset.editable ? (
    <>
      <Button variant="danger" className="mr-auto" onClick={askToDelete}>
        <Trash2 className="size-4" aria-hidden /> Delete…
      </Button>
      <a
        href={api.assetArtUrl(asset.name)}
        download={asset.file}
        className="inline-flex h-[var(--ctl-h)] items-center justify-center gap-1.5 rounded-ctl border border-line bg-raised px-3 text-[13px] font-medium text-fg transition-colors hover:border-line-strong hover:bg-hover"
      >
        <Download className="size-4" aria-hidden /> Download art
      </a>
      <Button ref={editRef} variant="primary" onClick={startEdit}>
        <Pencil className="size-4" aria-hidden /> Edit
      </Button>
    </>
  ) : undefined // nothing to do to a built-in asset here: the dialog's own close button is enough

  return (
    <>
      <Dialog open onOpenChange={(o) => !o && !saving && onOpenChange(false)} size="xl" title={pretty} description={asset.summary || undefined} footer={footer}>
        <div className="grid gap-6 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <section aria-label="Previews" className="flex min-w-0 flex-col gap-3 md:sticky md:top-0 md:self-start">
            <StylePreviews
              styles={styles}
              srcFor={(style) => api.assetThumbUrl(asset, style, place ? timeOfDay : undefined, !place && trueScale)}
              subject={pretty}
              kind={asset.kind}
              trueScale={trueScale}
              onTrueScale={setTrueScale}
              timeOfDay={timeOfDay}
              onTimeOfDay={setTimeOfDay}
            />
            {editing && <p className="text-[11.5px] leading-snug text-faint">The previews show the saved asset; they change when you save.</p>}
          </section>

          {editing ? (
            <div className="flex min-w-0 flex-col gap-4">
              {error && (
                <Banner tone="danger" title="It could not be saved">
                  <p className="first-letter:uppercase">{error.detail}</p>
                  {error.hint && <p className="first-letter:uppercase">{error.hint}</p>}
                </Banner>
              )}
              <AssetSettings
                value={form}
                onChange={(next) => {
                  if (nameError && next.name !== form.name) setNameError(null)
                  setForm(next)
                }}
                picture={picture}
                nameRef={nameRef}
                disabled={saving}
                lockAutoBackground={typeof asset.cutout === 'boolean'}
                nameError={nameError}
                nameNotice={form.name !== asset.name && !nameError ? <p className="text-[11.5px] leading-snug text-warning">Reels that use “{asset.name}” will not find it under the new name.</p> : null}
                onSubmit={() => void save()}
              />
            </div>
          ) : (
            <div className="flex min-w-0 flex-col gap-5">
              <div className="flex flex-wrap items-center gap-1.5">
                <Chip tone="neutral">{KIND_INFO[asset.kind].label}</Chip>
                <Chip tone={asset.editable ? 'accent' : 'neutral'}>{origin}</Chip>
                <Chip className="font-mono" title="the name a spec uses">
                  {asset.name}
                </Chip>
              </div>

              {error && (
                <Banner tone="danger" title="That did not work">
                  <p className="first-letter:uppercase">{error.detail}</p>
                  {error.hint && <p className="first-letter:uppercase">{error.hint}</p>}
                </Banner>
              )}

              {asset.origin === 'builtin' && (
                <Banner
                  title="Built-in assets are read-only"
                  action={
                    onOverride ? (
                      <Button size="sm" onClick={() => onOverride(asset)}>
                        Make my own version
                      </Button>
                    ) : undefined
                  }
                >
                  To change one, add your own with the same name, “{asset.name}”: it takes the built-in one’s place in every reel.
                </Banner>
              )}
              {asset.origin === 'user' && !asset.editable && (
                <Banner title="This one lives outside your workspace">
                  It comes from another assets folder (set with <code>--assets</code> or <code>REEL_ASSETS</code>), so it is read-only here. Change its files there.
                </Banner>
              )}

              <Facts rows={facts} />

              {asset.warnings && asset.warnings.length > 0 && (
                <Banner tone="warning" title="Parts of the drawing that are not drawn">
                  <ul className="m-0 list-disc pl-4">
                    {asset.warnings.map((w, i) => (
                      <li key={`${i}:${w}`}>{w}</li>
                    ))}
                  </ul>
                </Banner>
              )}

              <Section title="Colours a spec can change">
                {roles.length > 0 ? (
                  <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1.5 p-0">
                    {roles.map((r) => {
                      const color = row?.palette?.[r]
                      return (
                        <li key={r} className="flex items-center gap-1.5 text-[12.5px]">
                          {color ? <span aria-hidden className="size-4 rounded-full border border-black/15" style={{ background: color }} /> : <span aria-hidden className="size-4 rounded-full border border-dashed border-line-strong" />}
                          <span className="font-mono">{r}</span>
                          {color && <span className="font-mono text-[11px] text-faint">{color}</span>}
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <p className="text-[12.5px] text-muted">None: this drawing keeps its own colours.</p>
                )}
              </Section>

              <Section title="Words that find it">
                {asset.tags.length > 0 ? (
                  <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
                    {asset.tags.map((t) => (
                      <li key={t}>
                        <Chip dir="auto">{t}</Chip>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-[12.5px] text-muted">No words yet: only its name finds it.</p>
                )}
              </Section>

              <Section title="Use it in a spec">
                <p className="text-[12px] text-muted">
                  Under <code className="font-mono text-fg">{snippet.where}</code>
                </p>
                <div className="flex items-start gap-2">
                  <pre className="m-0 min-w-0 flex-1 overflow-x-auto rounded-ctl border border-line bg-raised px-2.5 py-2 font-mono text-[11.5px] leading-snug">{snippet.json}</pre>
                  {canCopy && (
                    <Button size="icon-sm" variant="ghost" aria-label="Copy the spec line" title="Copy the spec line" onClick={() => void copy()}>
                      <Copy className="size-3.5" aria-hidden />
                    </Button>
                  )}
                </div>
              </Section>
            </div>
          )}
        </div>
      </Dialog>

      <Dialog
        open={confirming}
        onOpenChange={(o) => !o && !deleting && setConfirming(false)}
        size="sm"
        title={`Delete “${asset.name}”?`}
        description="Its picture and its settings are removed from your assets folder."
        footer={
          <>
            <Button onClick={() => setConfirming(false)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void remove()} disabled={deleting}>
              {deleting && <Spinner />}
              Delete asset
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3">
          {deleteError && (
            <Banner tone="danger" title="It could not be deleted">
              <p className="first-letter:uppercase">{deleteError.detail}</p>
              {deleteError.hint && <p className="first-letter:uppercase">{deleteError.hint}</p>}
            </Banner>
          )}
          <p className="flex items-start gap-2 text-muted">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
            Reels that still use it will report it as missing, and the planners stop offering it. This cannot be undone from here.
          </p>
        </div>
      </Dialog>
    </>
  )
}
