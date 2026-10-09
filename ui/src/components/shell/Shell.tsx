import { useQueryClient } from '@tanstack/react-query'
import { Activity, AudioLines, Check, Clapperboard, FolderOpen, LayoutGrid, Library, Monitor, Moon, Plus, Sun } from 'lucide-react'
import { useEffect } from 'react'
import { NavLink, Outlet, useLocation, useMatch, useNavigate, useResolvedPath } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useHealth, useProjects } from '@/api/hooks'
import { ApiError } from '@/api/types'
import { Banner, Button, IconButton, Menu, ProgressBar, Spinner, Tip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { useJobs } from '@/store/jobs'
import { useProject } from '@/store/project'
import { useUi, type ThemePref } from '@/store/ui'
import { useStudio } from '@/store/studio'
import { useNarrow } from '@/lib/useMedia'
import { announceRender } from '@/features/export/finished'
import { useHotkeys } from '@/lib/hotkeys'
import { ShortcutsDialog } from './ShortcutsDialog'
import { VoiceConfirm } from './VoiceConfirm'

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn('size-8', className)} aria-hidden>
      <rect width="32" height="32" rx="9" className="fill-solid" />
      <path d="M12 9.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L13.5 8.64A1 1 0 0 0 12 9.5Z" fill="#fff" />
    </svg>
  )
}

function RailLink({ to, icon: Icon, label, end, disabled, bottom }: { to: string; icon: typeof LayoutGrid; label: string; end?: boolean; disabled?: boolean; bottom?: boolean }) {
  // the active state is worked out here, not with NavLink's className function: the tooltip's trigger merges className as a string
  const active = !!useMatch({ path: useResolvedPath(to).pathname, end: !!end }) && !disabled
  return (
    <Tip label={label} side={bottom ? 'top' : 'right'}>
      <NavLink
        to={to}
        end={end}
        aria-label={label}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        onClick={(e) => disabled && e.preventDefault()}
        className={cn(
          'grid size-10 place-items-center rounded-[10px] text-muted transition-colors hover:bg-hover hover:text-fg',
          active && 'bg-accent-soft text-accent hover:bg-accent-soft hover:text-accent',
          disabled && 'pointer-events-none opacity-35',
        )}
      >
        <Icon className="size-[19px]" strokeWidth={1.8} />
      </NavLink>
    </Tip>
  )
}

function ThemeMenu() {
  const { theme, setTheme } = useUi()
  const Icon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  const mark = (t: ThemePref) => (theme === t ? '✓ ' : '')
  return (
    <Menu
      align="start"
      trigger={
        <IconButton label="Theme" className="size-10 rounded-[10px]">
          <Icon className="size-[18px]" strokeWidth={1.8} />
        </IconButton>
      }
      entries={[
        { label: `${mark('system')}Match system`, icon: <Monitor />, onSelect: () => setTheme('system') },
        { label: `${mark('dark')}Dark`, icon: <Moon />, onSelect: () => setTheme('dark') },
        { label: `${mark('light')}Light`, icon: <Sun />, onSelect: () => setTheme('light') },
      ]}
    />
  )
}

/** The project you are in, and the others to jump to: opens a project in the same screen (Studio or Voice & Audio) you are on. */
function ProjectSwitcher() {
  const projects = useProjects()
  const nav = useNavigate()
  const loc = useLocation()
  const match = useMatch('/p/:id/*')
  const current = match?.params.id
  const onAudio = loc.pathname.endsWith('/audio')
  const list = [...(projects.data ?? [])].filter((p) => !p.broken).sort((a, b) => b.updated_at - a.updated_at).slice(0, 8)
  const open = (id: string) => nav(`/p/${encodeURIComponent(id)}${onAudio ? '/audio' : ''}`)
  return (
    <Menu
      align="start"
      trigger={
        <IconButton label="Switch project" className="size-10 rounded-[10px]">
          <FolderOpen className="size-[18px]" strokeWidth={1.8} />
        </IconButton>
      }
      entries={[
        { heading: 'Switch project' },
        ...(list.length ? list.map((p) => ({ label: p.title || p.id, icon: p.id === current ? <Check /> : undefined, onSelect: () => open(p.id) })) : [{ label: projects.isLoading ? 'Loading…' : 'No projects yet', disabled: true }]),
        { separator: true },
        { label: 'All projects', icon: <LayoutGrid />, onSelect: () => nav('/') },
        { label: 'New reel…', icon: <Plus />, onSelect: () => nav('/new') },
      ]}
    />
  )
}

function Rail({ bottom = false }: { bottom?: boolean }) {
  const match = useMatch('/p/:id/*')
  const last = useUi((s) => s.lastProject)
  const id = match?.params.id ?? last
  return (
    <nav aria-label="Main" className={cn('flex shrink-0 items-center gap-1 bg-panel', bottom ? 'h-12 w-full justify-around border-t border-line px-2' : 'w-14 flex-col border-r border-line py-3')}>
      {!bottom && (
        <NavLink to="/" aria-label="Reel Studio home" className="mb-3">
          <Logo />
        </NavLink>
      )}
      <RailLink to="/" end icon={LayoutGrid} label="Projects" bottom={bottom} />
      <RailLink to={id ? `/p/${id}` : '/'} end icon={Clapperboard} label={id ? 'Studio' : 'Studio (open a project first)'} disabled={!id} bottom={bottom} />
      <RailLink to={id ? `/p/${id}/audio` : '/'} icon={AudioLines} label={id ? 'Voice & Audio' : 'Voice & Audio (open a project first)'} disabled={!id} bottom={bottom} />
      <RailLink to="/library" icon={Library} label="Library" bottom={bottom} />
      <RailLink to="/health" icon={Activity} label="Health & Settings" bottom={bottom} />
      {!bottom && <div className="flex-1" />}
      {!bottom && <ProjectSwitcher />}
      <ThemeMenu />
    </nav>
  )
}

function StatusBar() {
  const health = useHealth()
  const jobs = useJobs((s) => s.jobs)
  const order = useJobs((s) => s.order)
  const active = order.map((id) => jobs[id]).filter((j) => j && (j.status === 'running' || j.status === 'queued'))
  const spec = useProject((s) => s.spec)
  const setStudio = useStudio((s) => s.set)
  const nav = useNavigate()
  const ok = health.data?.ffmpeg_ok
  const first = active[0]
  return (
    <footer className="flex h-7 shrink-0 items-center gap-4 border-t border-line bg-panel px-3 text-[11.5px] text-muted">
      <span className="flex items-center gap-1.5" title={health.data ? `${health.data.ffmpeg}\nskia ${health.data.skia}` : undefined}>
        <span className={cn('size-1.5 rounded-full', health.isError ? 'bg-danger' : ok === false ? 'bg-warning' : 'bg-success')} aria-hidden />
        {health.isError ? 'Engine not reachable' : ok === false ? 'ffmpeg missing' : health.data ? `Engine ready · reel ${health.data.version}` : 'Connecting…'}
      </span>
      {health.data && (
        <button className="hidden max-w-[40ch] truncate font-mono text-faint hover:text-fg md:block" title={`Workspace: ${health.data.workspace}`} onClick={() => void navigator.clipboard?.writeText(health.data.workspace)}>
          {health.data.workspace}
        </button>
      )}
      <div className="flex-1" />
      {spec && (
        <Button variant="ghost" size="sm" className="h-5 px-1.5 text-[11.5px]" onClick={() => setStudio({ problemsOpen: true })}>
          Problems
        </Button>
      )}
      {first ? (
        <button className="flex items-center gap-2 hover:text-fg" onClick={() => first.projectId && nav(`/p/${first.projectId}`)}>
          <Spinner className="size-3" />
          <span>
            {first.title}
            {first.status === 'queued' ? ' · queued' : first.total > 1 ? ` · ${Math.round((first.done / first.total) * 100)}%` : ''}
          </span>
          {first.status === 'running' && first.total > 1 && <ProgressBar value={first.done} max={first.total} className="w-24" label={first.title} />}
        </button>
      ) : (
        <span className="text-faint">No jobs running</span>
      )}
    </footer>
  )
}

function Locked() {
  return (
    <div className="grid h-full place-items-center p-8">
      <div className="max-w-md text-center">
        <Logo className="mx-auto mb-5 size-12" />
        <h1 className="mb-2 text-[18px] font-semibold tracking-tight">Open Reel Studio from its launch link</h1>
        <p className="text-muted">
          This window has no access token. Run <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-[12px]">reel serve --open</code> and use the link it prints: the token in it is what keeps the
          server yours alone.
        </p>
      </div>
    </div>
  )
}

export function Shell() {
  const health = useHealth()
  const loc = useLocation()
  const narrow = useNarrow()
  const setStudio = useStudio((s) => s.set)
  const api = useApi()
  const qc = useQueryClient()
  const nav = useNavigate()
  // a render that was running when the page was reloaded is still going on the server: pick it up again
  useEffect(() => {
    void useJobs.getState().resume(api, (info, result) => announceRender(qc, nav, info.projectId, result))
  }, [api, qc, nav])
  // the question mark shows the shortcuts (on a US keyboard it is Shift+/; elsewhere it may be a key of its own)
  useHotkeys({ 'shift+?': () => setStudio({ shortcutsOpen: true }), '?': () => setStudio({ shortcutsOpen: true }) })
  const err = health.error
  if (err instanceof ApiError && (err.status === 401 || err.status === 421)) return <Locked />
  const offline = err instanceof ApiError && err.status === 0
  return (
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        {!narrow && <Rail />}
        <main id="main" className="relative flex min-w-0 flex-1 flex-col" key={loc.pathname.startsWith('/p/') ? 'project' : loc.pathname}>
          {offline && (
            <div className="p-3 pb-0">
              <Banner tone="danger" title="Cannot reach the Reel Studio server" action={<Button size="sm" onClick={() => void health.refetch()}>Retry now</Button>}>
                Is <code className="font-mono">reel serve</code> still running? Retrying every few seconds; your open project stays on screen.
              </Banner>
            </div>
          )}
          <Outlet />
        </main>
      </div>
      <StatusBar />
      {narrow && <Rail bottom />}
      <VoiceConfirm />
      <ShortcutsDialog />
    </div>
  )
}

