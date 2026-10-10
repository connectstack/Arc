import { Suspense, lazy, useEffect } from 'react'
import { useCatalog } from '@/api/hooks'
import type { ReelSpec } from '@/api/types'
import { Sheet, Tabs } from '@/components/ui'
import { useHotkeys } from '@/lib/hotkeys'
import { useWide } from '@/lib/useMedia'
import { useChosenEngine } from '@/features/audio/useGenerateVoice'
import { isFresh, useAudio } from '@/store/audio'
import { LintRunner } from '@/store/lint'
import { useProject } from '@/store/project'
import { useStudio, type RightTab } from '@/store/studio'
import { useUi } from '@/store/ui'
import { Inspector } from './inspector/Inspector'
import { LeftPanel } from './LeftPanel'
import { Preview, usePreviewSession } from './Preview'
import { ProblemsDrawer } from './ProblemsDrawer'
import { Splitter } from './Splitter'
import { Timeline } from './Timeline'
import { useClipboard } from '@/store/clipboard'
import { copySelection, deleteSelected, duplicateSelected, nudgeSelected, pasteAtPlayhead } from './clipboardActions'
import { addCaption, splitAtPlayhead } from './ops'
import { clamp } from '@/lib/timeline'

/** The narrowest the left panel goes: its five tabs (Build, Scenes, Cast, Script, Library) need this much to be read. */
const LEFT_MIN = 264

// the JSON editor (CodeMirror) is a third of the app's code and is only opened on request
const JsonPanel = lazy(() => import('./JsonPanel').then((m) => ({ default: m.JsonPanel })))

/** Copy only when no text is selected on the page: otherwise the key is the browser's (copying a problem message, say). */
const pageTextSelected = (): boolean => !!window.getSelection()?.toString()

function useStudioHotkeys() {
  useHotkeys({
    delete: () => deleteSelected(),
    backspace: () => deleteSelected(),
    'mod+d': () => duplicateSelected(),
    'mod+c': () => {
      if (pageTextSelected() || !copySelection()) return false
    },
    'mod+x': () => {
      if (pageTextSelected() || !copySelection(true)) return false
    },
    'mod+v': () => {
      if (!useClipboard.getState().board) return false // nothing of ours to paste: leave it to the browser
      void pasteAtPlayhead()
    },
    s: () => {
      const st = useProject.getState()
      st.edit((d) => st.select(splitAtPlayhead(d, st.selection, st.playhead)))
    },
    'alt+left': () => nudgeSelected(-1 / (useProject.getState().spec?.meta.fps ?? 30)),
    'alt+right': () => nudgeSelected(1 / (useProject.getState().spec?.meta.fps ?? 30)),
    c: () => {
      const st = useProject.getState()
      st.edit((d) => {
        const s = addCaption(d, st.playhead)
        if (s) st.select(s)
      })
    },
    escape: () => {
      const st = useStudio.getState()
      if (st.problemsOpen) st.set({ problemsOpen: false })
      else useProject.getState().select({ kind: 'reel' })
    },
    'mod+\\': () => {
      const ui = useUi.getState()
      const both = ui.leftOpen && ui.rightOpen
      ui.set({ leftOpen: !both, rightOpen: !both })
    },
  })
}

export function StudioPage() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const ui = useUi()
  const studio = useStudio()
  const catalog = useCatalog()
  const wide = useWide()
  useStudioHotkeys()
  useEffect(() => () => useStudio.getState().set({ playing: false, problemsOpen: false, drawer: null }), [])
  // a drawer left open when the window grows wide enough for docked panels would be stuck
  useEffect(() => {
    if (wide) useStudio.getState().set({ drawer: null })
  }, [wide])
  // once the voice exists the preview is the retimed reel with the mouths on the words, as the final render will be
  const audio = useAudio()
  const projectId = useProject((s) => s.id)
  const { name: engineName } = useChosenEngine()
  const voiced = audio.projectId === projectId && isFresh(spec, audio, engineName) && !!audio.result?.spec
  const session = usePreviewSession(voiced ? (audio.result?.spec as unknown as ReelSpec) : spec, voiced ? audio.result?.word_timings : undefined)

  const leftPanel = catalog.isLoading ? <p className="p-4 text-muted">Loading the catalog…</p> : <LeftPanel />
  const rightPanel = (
    <>
      <Tabs
        label="Inspector panels"
        value={studio.rightTab}
        onChange={(v) => studio.set({ rightTab: v as RightTab })}
        tabs={[
          { value: 'inspector', label: 'Inspector' },
          { value: 'json', label: 'JSON' },
        ]}
      >
        {studio.rightTab === 'inspector' ? (
          <Inspector />
        ) : (
          <Suspense fallback={<p className="p-4 text-muted">Loading the editor…</p>}>
            <JsonPanel />
          </Suspense>
        )}
      </Tabs>
    </>
  )

  return (
    <div className="flex min-h-0 flex-1">
      <h1 className="sr-only">Studio: {spec.meta.title}</h1>
      <LintRunner />
      {wide && ui.leftOpen && (
        <>
          <aside style={{ width: Math.max(ui.leftW, LEFT_MIN) }} className="flex shrink-0 flex-col bg-panel" aria-label="Build, scenes, cast, script and library">
            {leftPanel}
          </aside>
          <Splitter dir="x" label="Resize the left panel" value={Math.max(ui.leftW, LEFT_MIN)} min={LEFT_MIN} max={460} onDrag={(dx) => ui.set({ leftW: clamp(Math.max(ui.leftW, LEFT_MIN) + dx, LEFT_MIN, 460) })} />
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 flex-col">
          <Preview session={session} />
        </div>
        <Splitter dir="y" label="Resize the timeline" value={ui.timelineH} min={180} max={640} onDrag={(dy) => ui.set({ timelineH: clamp(ui.timelineH - dy, 180, 640) })} />
        <div className="relative flex shrink-0 flex-col" style={{ height: ui.timelineH }}>
          <Timeline />
          {studio.problemsOpen && <ProblemsDrawer />}
        </div>
      </div>

      {wide && ui.rightOpen && (
        <>
          <Splitter dir="x" label="Resize the inspector" value={ui.rightW} min={280} max={560} onDrag={(dx) => ui.set({ rightW: clamp(ui.rightW - dx, 280, 560) })} />
          <aside style={{ width: ui.rightW }} className="flex shrink-0 flex-col bg-panel" aria-label="Inspector">
            {rightPanel}
          </aside>
        </>
      )}

      {!wide && (
        <>
          <Sheet open={studio.drawer === 'left'} onOpenChange={(o) => studio.set({ drawer: o ? 'left' : null })} side="left" title="Build, scenes, cast, script and library">
            {leftPanel}
          </Sheet>
          <Sheet open={studio.drawer === 'right'} onOpenChange={(o) => studio.set({ drawer: o ? 'right' : null })} side="right" title="Inspector">
            {rightPanel}
          </Sheet>
        </>
      )}
    </div>
  )
}
