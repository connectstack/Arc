import { AlertTriangle, ArrowLeft, Check, ChevronDown, CircleAlert, Clapperboard, Command, Keyboard, Loader2, Redo2, Undo2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Outlet, useNavigate, useParams } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import { ApiError } from '@/api/types'
import { Banner, Button, Dialog, EmptyState, IconButton, Input, Menu, Skeleton, Tip } from '@/components/ui'
import { ExportDialog } from '@/features/export/ExportDialog'
import { useAutosave } from './useAutosave'
import { CommandPalette } from './CommandPalette'
import { HistoryButton } from './HistoryPopover'
import { useHotkeys, MOD } from '@/lib/hotkeys'
import { cn } from '@/lib/cn'
import { describeChange } from '@/lib/describe'
import { normalizeSpec } from '@/lib/normalize'
import { useProject, type SaveState } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'

function SaveIndicator({ onRetry }: { onRetry: () => void }) {
  const save = useProject((s) => s.save)
  const error = useProject((s) => s.saveError)
  const map: Record<SaveState, { icon: React.ReactNode; text: string; tone: string }> = {
    idle: { icon: null, text: '', tone: 'text-faint' },
    saved: { icon: <Check className="size-3.5" />, text: 'Saved', tone: 'text-muted' },
    dirty: { icon: <span className="size-1.5 rounded-full bg-warning" />, text: 'Unsaved changes', tone: 'text-muted' },
    saving: { icon: <Loader2 className="size-3.5 animate-[spin_0.9s_linear_infinite]" />, text: 'Saving…', tone: 'text-muted' },
    error: { icon: <CircleAlert className="size-3.5" />, text: 'Save failed', tone: 'text-danger' },
    conflict: { icon: <AlertTriangle className="size-3.5" />, text: 'Changed on disk', tone: 'text-warning' },
  }
  const m = map[save]
  return (
    <span className={cn('flex shrink-0 items-center gap-1.5 text-[12px]', m.tone)} role="status" title={error ?? undefined} aria-label={m.text || undefined}>
      {m.icon}
      <span className="hidden sm:inline">{m.text}</span>
      {save === 'error' && (
        <button className="ml-1 font-medium text-accent hover:underline" onClick={onRetry}>
          Retry
        </button>
      )}
    </span>
  )
}

function StyleSwitcher() {
  const api = useApi()
  const catalog = useCatalog()
  const style = useProject((s) => s.spec?.meta.style)
  const edit = useProject((s) => s.edit)
  const styles = catalog.data?.styles ?? []
  return (
    <div role="radiogroup" aria-label="Visual style" className="flex items-center gap-1 rounded-ctl border border-line bg-raised p-0.5">
      {styles.map((st) => {
        const on = st.name === style
        return (
          <Tip key={st.name} label={`${st.name.replace('_', ' ')}: ${st.summary}`}>
            <button
              role="radio"
              aria-checked={on}
              aria-label={st.name.replace('_', ' ')}
              onClick={() => edit((d) => void (d.meta.style = st.name))}
              className={cn('flex h-7 items-center gap-1.5 rounded-[6px] pl-0.5 pr-2 text-[12px] font-medium transition-colors', on ? 'bg-accent-soft text-accent' : 'text-muted hover:text-fg')}
            >
              <img src={api.libraryThumbUrl('style', st.name, 'day')} alt="" loading="lazy" className={cn('h-6 w-[14px] rounded-[3px] object-cover', on ? 'ring-1 ring-accent' : '')} />
              <span className="hidden xl:inline">{st.name.replace('_', ' ')}</span>
            </button>
          </Tip>
        )
      })}
    </div>
  )
}

function RenderButton() {
  const set = useStudio((s) => s.set)
  const open = (preset: 'draft' | 'standard' | 'full' | 'custom') => set({ exportOpen: true, exportPreset: preset })
  return (
    <div className="flex">
      <Button variant="primary" className="rounded-r-none" onClick={() => open('draft')}>
        <Clapperboard className="size-4" /> Render
      </Button>
      <Menu
        trigger={
          <Button variant="primary" size="icon" aria-label="More render options" className="w-7 rounded-l-none border-l border-white/20">
            <ChevronDown className="size-4" />
          </Button>
        }
        entries={[
          { heading: 'Render a video' },
          { label: 'Draft · 360×640 (fast)', onSelect: () => open('draft'), shortcut: `${MOD}↵` },
          { label: 'Standard · 540×960', onSelect: () => open('standard') },
          { label: 'Full HD · 1080×1920', onSelect: () => open('full') },
          { separator: true },
          { label: 'Custom…', onSelect: () => open('custom') },
        ]}
      />
    </div>
  )
}

function TopBar({ onBack, saveNow }: { onBack: () => void; saveNow: () => void }) {
  const title = useProject((s) => s.spec?.meta.title ?? '')
  const edit = useProject((s) => s.edit)
  const canUndo = useProject((s) => s.past.length > 0)
  const canRedo = useProject((s) => s.future.length > 0)
  const undo = useProject((s) => s.undo)
  const redo = useProject((s) => s.redo)
  const set = useStudio((s) => s.set)
  const [text, setText] = useState(title)
  useEffect(() => setText(title), [title])
  const commit = () => {
    const t = text.trim()
    if (t && t !== title) edit((d) => void (d.meta.title = t))
    else setText(title)
  }
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-panel px-2 sm:gap-3 sm:px-3">
      <IconButton label="All projects" onClick={onBack}>
        <ArrowLeft className="size-4" />
      </IconButton>
      <Input
        value={text}
        aria-label="Reel title"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        className="h-8 min-w-0 flex-1 border-transparent bg-transparent text-[14px] font-semibold hover:bg-raised sm:w-[min(30vw,340px)] sm:flex-none"
      />
      <SaveIndicator onRetry={saveNow} />
      <div className="hidden flex-1 sm:block" />
      <div className="hidden lg:flex">
        <StyleSwitcher />
      </div>
      <div className="mx-1 hidden h-5 w-px bg-line lg:block" />
      <Tip label="Undo" shortcut={`${MOD}Z`}>
        <IconButton label="Undo" disabled={!canUndo} onClick={undo} className="hidden sm:inline-flex">
          <Undo2 className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Redo" shortcut={`⇧${MOD}Z`}>
        <IconButton label="Redo" disabled={!canRedo} onClick={redo} className="hidden sm:inline-flex">
          <Redo2 className="size-4" />
        </IconButton>
      </Tip>
      <HistoryButton />
      <Tip label="Command palette" shortcut={`${MOD}K`}>
        <IconButton label="Command palette" onClick={() => set({ paletteOpen: true })}>
          <Command className="size-4" />
        </IconButton>
      </Tip>
      <Tip label="Keyboard shortcuts" shortcut="?">
        <IconButton label="Keyboard shortcuts" onClick={() => set({ shortcutsOpen: true })} className="hidden lg:inline-flex">
          <Keyboard className="size-4" />
        </IconButton>
      </Tip>
      <RenderButton />
    </header>
  )
}

export function ConflictDialog() {
  const conflict = useProject((s) => s.conflict)
  const spec = useProject((s) => s.spec)
  const saved = useProject((s) => s.savedSpec)
  const resolve = useProject((s) => s.resolveConflict)
  // what each side did since the version both started from, in the words of the undo history
  const yours = spec && saved ? describeChange(saved, spec) : ''
  const theirs = conflict && saved ? describeChange(saved, normalizeSpec(conflict.spec)) : ''
  return (
    <Dialog
      open={!!conflict}
      onOpenChange={() => undefined}
      title="This project changed on disk"
      description="Something else (another tab, or an editor) saved this file after you opened it."
      footer={
        <>
          <Button onClick={() => resolve('disk')}>Load the disk version</Button>
          <Button variant="primary" onClick={() => resolve('mine')}>
            Keep my edits and overwrite
          </Button>
        </>
      }
    >
      <dl className="m-0 mb-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-card border border-line bg-raised p-3 text-[13px]">
        <dt className="text-muted">On disk</dt>
        <dd className="m-0 font-medium">{theirs || 'a different version'}</dd>
        <dt className="text-muted">Your edits</dt>
        <dd className="m-0 font-medium">{yours || 'none'}</dd>
      </dl>
      <p className="text-muted">Loading the disk version drops the edits you made here since the last save. Overwriting keeps your version and replaces the file.</p>
    </Dialog>
  )
}

export function ProjectLayout() {
  const { id = '' } = useParams()
  const api = useApi()
  const nav = useNavigate()
  const catalog = useCatalog()
  const { saveNow } = useAutosave()
  const loaded = useProject((s) => s.id === id && s.spec !== null)
  const [error, setError] = useState<ApiError | null>(null)
  const set = useStudio((s) => s.set)
  const exportOpen = useStudio((s) => s.exportOpen)
  const undo = useProject((s) => s.undo)
  const redo = useProject((s) => s.redo)

  useEffect(() => {
    let cancelled = false
    setError(null)
    api
      .getProject(id)
      .then((doc) => {
        if (cancelled) return
        useProject.getState().load(doc)
        useUi.getState().set({ lastProject: id })
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e : new ApiError(0, String(e))))
    return () => {
      cancelled = true
      useProject.getState().unload()
    }
  }, [api, id])

  // the command palette's "Save now" asks for a save this way
  useEffect(() => {
    const onSave = () => void saveNow()
    document.addEventListener('reel:save', onSave)
    return () => document.removeEventListener('reel:save', onSave)
  }, [saveNow])

  useHotkeys({
    'mod+z': () => undo(),
    'mod+shift+z': () => redo(),
    'mod+y': () => redo(),
    'mod+s': () => void saveNow(),
    'mod+k': () => set({ paletteOpen: true }),
    'mod+enter': () => set({ exportOpen: true, exportPreset: 'draft' }),
  })

  if (error)
    return (
      <EmptyState
        art={<AlertTriangle className="size-9 text-warning" strokeWidth={1.4} />}
        title={error.status === 404 ? 'That project does not exist' : 'This project cannot be opened'}
        action={<Button onClick={() => nav('/')}>Back to projects</Button>}
        className="flex-1"
      >
        {error.detail}
        {error.hint ? ` ${error.hint}` : ''}
      </EmptyState>
    )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TopBar onBack={() => nav('/')} saveNow={() => void saveNow()} />
      {catalog.isError && (
        <div className="p-3 pb-0">
          <Banner tone="danger" title="The catalog could not be loaded">
            Actions, backgrounds and styles come from the server; try reloading the page.
          </Banner>
        </div>
      )}
      {loaded ? (
        <Outlet />
      ) : (
        <div className="grid flex-1 place-items-center" aria-busy>
          <div className="flex w-[min(520px,80vw)] flex-col gap-3">
            <Skeleton className="h-6 w-1/3" />
            <Skeleton className="h-64" />
            <Skeleton className="h-24" />
          </div>
        </div>
      )}
      <ConflictDialog />
      {loaded && <CommandPalette />}
      {loaded && exportOpen && <ExportDialog />}
    </div>
  )
}

