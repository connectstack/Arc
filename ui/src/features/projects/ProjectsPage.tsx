import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Clapperboard, Copy, Download, FileJson, Film, ImageOff, MoreHorizontal, Pencil, PenLine, Sparkles, Trash2, Wand2 } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useExamples, useProjects, useRenders } from '@/api/hooks'
import { ApiError } from '@/api/types'
import type { ProjectSummary, RenderResult } from '@/api/types'
import { Page, PageHeader } from '@/components/shell/PageHeader'
import { Button, Chip, Dialog, EmptyState, Input, Menu, Skeleton, StatusChip, toast } from '@/components/ui'
import { ago, bytes, plural, seconds } from '@/lib/format'
import { budgetState } from '@/lib/timeline'
import { cn } from '@/lib/cn'

function StartCard({ icon, title, text, onClick, primary }: { icon: ReactNode; title: string; text: string; onClick: () => void; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'group flex flex-1 items-start gap-3 rounded-card border p-4 text-left transition-colors',
        primary ? 'border-accent/40 bg-accent-soft hover:border-accent' : 'border-line bg-panel hover:border-line-strong hover:bg-raised',
      )}
    >
      <span className={cn('grid size-9 shrink-0 place-items-center rounded-[10px] [&_svg]:size-[18px]', primary ? 'bg-solid text-accent-fg' : 'bg-hover text-muted group-hover:text-fg')}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-[14px] font-semibold">{title}</span>
        <span className="mt-0.5 block text-muted">{text}</span>
      </span>
    </button>
  )
}

function Thumb({ src, alt }: { src: string; alt: string }) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  if (failed)
    return (
      <div className="grid size-full place-items-center bg-hover text-faint">
        <ImageOff className="size-6" aria-label="No preview yet" />
      </div>
    )
  return (
    <>
      {!loaded && <Skeleton className="absolute inset-0 rounded-none" />}
      <img src={src} alt={alt} loading="lazy" onLoad={() => setLoaded(true)} onError={() => setFailed(true)} className={cn('size-full object-cover transition-opacity duration-200', loaded ? 'opacity-100' : 'opacity-0')} />
    </>
  )
}

function ProjectCard({ p, onOpen, onRename, onDuplicate, onExport, onDelete }: { p: ProjectSummary; onOpen: () => void; onRename: () => void; onDuplicate: () => void; onExport: () => void; onDelete: () => void }) {
  const api = useApi()
  const dur = p.duration_sec ?? 0
  const state = budgetState(dur)
  const errors = p.lint?.errors ?? 0
  const warnings = p.lint?.warnings ?? 0
  return (
    <article className="group relative flex flex-col overflow-hidden rounded-card border border-line bg-panel transition-colors hover:border-line-strong">
      <button onClick={onOpen} aria-label={`Open ${p.title}`} className="relative block aspect-[9/16] w-full overflow-hidden bg-hover">
        {p.broken ? (
          <div className="grid size-full place-items-center p-4 text-center text-[12px] text-danger">
            <span>
              <AlertTriangle className="mx-auto mb-1 size-5" />
              {p.broken}
            </span>
          </div>
        ) : (
          <Thumb src={api.projectThumbUrl(p)} alt={`Preview of ${p.title}`} />
        )}
      </button>
      <div className="flex flex-col gap-2 p-3">
        <div className="flex items-start justify-between gap-2">
          <button onClick={onOpen} className="min-w-0 text-left">
            <h3 className="truncate text-[14px] font-semibold leading-tight">{p.title}</h3>
            <p className="mt-0.5 text-[11.5px] text-faint">{p.updated_at ? ago(p.updated_at) : '—'}</p>
          </button>
          <Menu
            trigger={
              <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${p.title}`} className="-mr-1.5">
                <MoreHorizontal className="size-4" />
              </Button>
            }
            entries={[
              { label: 'Rename', icon: <Pencil />, onSelect: onRename },
              { label: 'Duplicate', icon: <Copy />, onSelect: onDuplicate },
              { label: 'Export JSON', icon: <Download />, onSelect: onExport },
              { separator: true },
              { label: 'Delete…', icon: <Trash2 />, danger: true, onSelect: onDelete },
            ]}
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {p.style && <Chip tone="accent">{p.style.replace('_', ' ')}</Chip>}
          {p.duration_sec !== undefined && (
            <StatusChip tone={state === 'out' ? 'danger' : 'neutral'}>
              <span className="tabular">{seconds(dur)}</span>
            </StatusChip>
          )}
          {errors > 0 ? <StatusChip tone="danger">{plural(errors, 'error')}</StatusChip> : warnings > 0 ? <StatusChip tone="warning">{plural(warnings, 'warning')}</StatusChip> : p.lint ? <StatusChip tone="success">Clean</StatusChip> : null}
        </div>
      </div>
    </article>
  )
}

function RenderCard({ r }: { r: RenderResult }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [url] = useState(() => r.video_url)
  if (!url) return null
  return (
    <a href={url} target="_blank" rel="noreferrer" className="group flex w-[132px] shrink-0 flex-col gap-1.5" onMouseEnter={() => void ref.current?.play().catch(() => undefined)} onMouseLeave={() => ref.current?.pause()}>
      <div className="aspect-[9/16] overflow-hidden rounded-card border border-line bg-hover">
        <video ref={ref} src={`${url}#t=0.1`} preload="metadata" muted playsInline loop className="size-full object-cover" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-[12px] font-medium">{r.title ?? r.id}</div>
        <div className="text-[11px] text-faint">
          {r.preset} · {bytes(r.size_bytes)}
        </div>
      </div>
    </a>
  )
}

export function ProjectsPage() {
  const api = useApi()
  const nav = useNavigate()
  const qc = useQueryClient()
  const projects = useProjects()
  const renders = useRenders()
  const examples = useExamples()
  const [exampleOpen, setExampleOpen] = useState(false)
  const [rename, setRename] = useState<ProjectSummary | null>(null)
  const [remove, setRemove] = useState<ProjectSummary | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const refresh = () => qc.invalidateQueries({ queryKey: ['projects'] })
  const create = useMutation({
    mutationFn: (body: Parameters<typeof api.createProject>[0]) => api.createProject(body),
    onSuccess: (doc) => {
      void refresh()
      nav(`/p/${doc.id}`)
    },
    onError: (e) => toast.error('Could not create the project', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e)),
  })

  const openFile = async (file: File) => {
    try {
      const spec = JSON.parse(await file.text())
      if (typeof spec !== 'object' || spec === null || Array.isArray(spec)) throw new Error('a spec is a JSON object')
      create.mutate({ spec, title: undefined })
    } catch (e) {
      toast.error(`${file.name} is not a spec`, e instanceof Error ? e.message : String(e))
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) void openFile(f)
  }

  const duplicate = async (p: ProjectSummary) => {
    const doc = await api.getProject(p.id)
    const copy = await api.createProject({ spec: { ...doc.spec, meta: { ...doc.spec.meta, title: `${p.title} copy` } }, script: doc.script, title: `${p.title} copy` })
    void refresh()
    toast.success('Duplicated', `Opened “${copy.spec.meta.title}”`)
    nav(`/p/${copy.id}`)
  }

  const exportJson = async (p: ProjectSummary) => {
    const doc = await api.getProject(p.id)
    const blob = new Blob([JSON.stringify(doc.spec, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${p.id}.reel.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 4000)
  }

  useEffect(() => {
    const over = (e: globalThis.DragEvent) => {
      if (e.dataTransfer?.types.includes('Files')) {
        e.preventDefault()
        setDragging(true)
      }
    }
    const leave = (e: globalThis.DragEvent) => e.relatedTarget === null && setDragging(false)
    window.addEventListener('dragover', over)
    window.addEventListener('dragleave', leave)
    return () => {
      window.removeEventListener('dragover', over)
      window.removeEventListener('dragleave', leave)
    }
  }, [])

  const list = projects.data ?? []
  const recent = (renders.data ?? []).filter((r) => r.video_url).slice(0, 8)

  return (
    <div className="flex min-h-0 flex-1 flex-col" onDrop={onDrop}>
      <PageHeader
        title="Projects"
        subtitle="Every reel is a spec file in your workspace folder: you own it, and the command line can render it too."
        actions={
          <Button variant="primary" onClick={() => nav('/new')}>
            <Wand2 className="size-4" /> New reel
          </Button>
        }
      />
      <Page>
        <div className="mx-auto max-w-[1280px] px-4 py-5 sm:px-6">
          <div className="grid gap-3 md:grid-cols-3">
            <StartCard primary icon={<PenLine />} title="From a script" text="Paste a script, pick a look, get a first cut in under a minute." onClick={() => nav('/new')} />
            <StartCard icon={<Sparkles />} title="From an example" text="Open a finished reel and see how it is built." onClick={() => setExampleOpen(true)} />
            <StartCard icon={<FileJson />} title="Open a spec file" text="Drop a .json spec anywhere on this page, or choose one." onClick={() => fileRef.current?.click()} />
            <input ref={fileRef} type="file" accept=".json,application/json" className="sr-only" aria-label="Choose a spec file" onChange={(e) => e.target.files?.[0] && void openFile(e.target.files[0])} />
          </div>

          <h2 className="eyebrow mb-3 mt-8">Your reels</h2>
          {projects.isLoading ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(176px,1fr))] gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="aspect-[9/19] rounded-card" />
              ))}
            </div>
          ) : projects.isError ? (
            <EmptyState title="Could not load your projects" action={<Button onClick={() => void projects.refetch()}>Try again</Button>}>
              {projects.error instanceof ApiError ? projects.error.detail : String(projects.error)}
            </EmptyState>
          ) : list.length === 0 ? (
            <EmptyState
              art={<Clapperboard className="size-10 text-accent" strokeWidth={1.4} />}
              title="No reels yet"
              action={
                <Button variant="primary" onClick={() => nav('/new')}>
                  <Wand2 className="size-4" /> Make your first reel
                </Button>
              }
            >
              Paste a script and the planner lays out scenes, characters and camera for you.
            </EmptyState>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(176px,1fr))] gap-4">
              {list.map((p) => (
                <ProjectCard key={p.id} p={p} onOpen={() => nav(`/p/${p.id}`)} onRename={() => setRename(p)} onDuplicate={() => void duplicate(p)} onExport={() => void exportJson(p)} onDelete={() => setRemove(p)} />
              ))}
            </div>
          )}

          {recent.length > 0 && (
            <>
              <h2 className="eyebrow mb-3 mt-9 flex items-center gap-2">
                <Film className="size-3.5" /> Recent renders
              </h2>
              <div className="flex gap-3 overflow-x-auto pb-2">
                {recent.map((r) => (
                  <RenderCard key={r.id} r={r} />
                ))}
              </div>
            </>
          )}
        </div>
      </Page>

      {dragging && (
        <div className="fade pointer-events-none absolute inset-0 z-30 grid place-items-center bg-bg/80 backdrop-blur-[2px]">
          <div className="rounded-[16px] border-2 border-dashed border-accent px-10 py-8 text-center">
            <FileJson className="mx-auto mb-2 size-8 text-accent" />
            <div className="text-[15px] font-semibold">Drop a spec to open it</div>
          </div>
        </div>
      )}

      <Dialog open={exampleOpen} onOpenChange={setExampleOpen} title="Start from an example" description="A copy is added to your workspace, so you can change anything.">
        <div className="grid gap-2">
          {(examples.data?.specs ?? []).map((ex) => (
            <button key={ex.id} onClick={() => create.mutate({ from_example: ex.id })} disabled={create.isPending} className="flex items-center justify-between gap-3 rounded-card border border-line bg-raised p-3 text-left hover:border-accent">
              <span>
                <span className="block font-semibold">{ex.title}</span>
                <span className="text-muted">
                  {ex.style.replace('_', ' ')} · {plural(ex.scenes, 'scene')} · {seconds(ex.duration_sec)}
                </span>
              </span>
              <Chip tone="accent">Open</Chip>
            </button>
          ))}
          {examples.data && examples.data.specs.length === 0 && <p className="text-muted">No examples were found next to this install. Run reel from a source checkout to get them.</p>}
        </div>
      </Dialog>

      <RenameDialog p={rename} onClose={() => setRename(null)} onDone={() => void refresh()} />
      <Dialog
        open={!!remove}
        onOpenChange={(o) => !o && setRemove(null)}
        title={`Delete “${remove?.title ?? ''}”?`}
        description="The spec file is removed from your workspace. Videos you rendered from it are kept."
        size="sm"
        footer={
          <>
            <Button onClick={() => setRemove(null)}>Cancel</Button>
            <Button
              variant="danger"
              onClick={async () => {
                if (!remove) return
                await api.deleteProject(remove.id)
                setRemove(null)
                void refresh()
                toast.success('Project deleted')
              }}
            >
              Delete project
            </Button>
          </>
        }
      >
        <p className="text-muted">This cannot be undone from here (the file goes through the API, not the Trash).</p>
      </Dialog>
    </div>
  )
}

function RenameDialog({ p, onClose, onDone }: { p: ProjectSummary | null; onClose: () => void; onDone: () => void }) {
  const api = useApi()
  const [title, setTitle] = useState('')
  useEffect(() => setTitle(p?.title ?? ''), [p])
  const save = async () => {
    if (!p || !title.trim()) return
    const doc = await api.getProject(p.id)
    await api.saveProject(p.id, { ...doc.spec, meta: { ...doc.spec.meta, title: title.trim() } }, undefined, doc.etag)
    onClose()
    onDone()
  }
  return (
    <Dialog
      open={!!p}
      onOpenChange={(o) => !o && onClose()}
      title="Rename reel"
      size="sm"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => void save()} disabled={!title.trim()}>
            Rename
          </Button>
        </>
      }
    >
      <Input autoFocus value={title} aria-label="Reel title" onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void save()} />
    </Dialog>
  )
}

