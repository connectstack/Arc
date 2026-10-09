import * as RDialog from '@radix-ui/react-dialog'
import { Activity, AudioLines, Boxes, Clapperboard, ClipboardPaste, Copy, Keyboard, Library, LayoutGrid, Moon, PanelLeft, PanelRight, Plus, Redo2, Save, Scissors, Search, Sun, Undo2, UserPlus, Wand2, Type, Film, Ruler } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCatalog } from '@/api/hooks'
import { Kbd } from '@/components/ui'
import { cn } from '@/lib/cn'
import { MOD } from '@/lib/hotkeys'
import { characterName } from '@/lib/spec'
import { fitToDuration, sceneSlots } from '@/lib/timeline'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { copySelection, pasteAtPlayhead } from './clipboardActions'
import { sizeText } from './ObjectPicker'
import { objectName } from './objects'
import { PERSON_HEIGHT } from '@/lib/spec'
import { addCaption, addCharacter, addObject, addScene } from './ops'

interface Command {
  id: string
  label: string
  hint?: string
  icon: ReactNode
  shortcut?: string
  group: string
  /** more words that find it (not shown): a library object's tags, its summary */
  keywords?: string
  /** listed only once something is typed (a library has too many objects to list them all) */
  searchOnly?: boolean
  run: () => void
}

export function CommandPalette() {
  const open = useStudio((s) => s.paletteOpen)
  const set = useStudio((s) => s.set)
  const nav = useNavigate()
  const spec = useProject((s) => s.spec)
  const id = useProject((s) => s.id)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const catalog = useCatalog().data

  const commands = useMemo<Command[]>(() => {
    const st = useProject.getState
    const close = () => set({ paletteOpen: false })
    const go = (to: string) => () => (close(), nav(to))
    const out: Command[] = [
      { id: 'nav-projects', group: 'Go to', label: 'Projects', icon: <LayoutGrid />, run: go('/') },
      { id: 'nav-studio', group: 'Go to', label: 'Studio', icon: <Clapperboard />, run: go(`/p/${id}`) },
      { id: 'nav-audio', group: 'Go to', label: 'Voice & Audio', icon: <AudioLines />, run: go(`/p/${id}/audio`) },
      { id: 'nav-library', group: 'Go to', label: 'Library', icon: <Library />, run: go('/library') },
      { id: 'nav-health', group: 'Go to', label: 'Health & Settings', icon: <Activity />, run: go('/health') },
      { id: 'undo', group: 'Edit', label: 'Undo', icon: <Undo2 />, shortcut: `${MOD}Z`, run: () => (close(), st().undo()) },
      { id: 'redo', group: 'Edit', label: 'Redo', icon: <Redo2 />, shortcut: `⇧${MOD}Z`, run: () => (close(), st().redo()) },
      { id: 'copy', group: 'Edit', label: 'Copy the selected clips', icon: <Copy />, shortcut: `${MOD}C`, run: () => (close(), void copySelection()) },
      { id: 'cut', group: 'Edit', label: 'Cut the selected clips', icon: <Scissors />, shortcut: `${MOD}X`, run: () => (close(), void copySelection(true)) },
      { id: 'paste', group: 'Edit', label: 'Paste at the playhead', hint: 'into the selected character’s lane', icon: <ClipboardPaste />, shortcut: `${MOD}V`, run: () => (close(), void pasteAtPlayhead()) },
      { id: 'save', group: 'Edit', label: 'Save now', icon: <Save />, shortcut: `${MOD}S`, run: () => (close(), document.dispatchEvent(new CustomEvent('reel:save'))) },
      { id: 'add-scene', group: 'Add', label: 'Add a scene at the end', icon: <Plus />, run: () => (close(), st().edit((d) => st().select(addScene(d)))) },
      { id: 'add-caption', group: 'Add', label: 'Add a caption at the playhead', icon: <Type />, run: () => (close(), st().edit((d) => { const s = addCaption(d, st().playhead); if (s) st().select(s) })) },
      { id: 'add-character', group: 'Add', label: 'Add a character', icon: <UserPlus />, run: () => (close(), st().edit((d) => st().select(addCharacter(d, 'everyman')))) },
      // lists the library's objects as "Add object: <name>" (they are too many to show unasked)
      { id: 'add-object', group: 'Add', label: 'Add object…', hint: 'a car, a tree, a cake … from the asset library', icon: <Boxes />, run: () => (setQ('Add object: '), requestAnimationFrame(() => inputRef.current?.focus())) },
      { id: 'fit', group: 'Edit', label: 'Fit the reel to 50 seconds', hint: 'scales every scene and what happens inside it', icon: <Ruler />, run: () => (close(), st().spec && st().replace(fitToDuration(st().spec as NonNullable<typeof spec>, 50))) },
      { id: 'render-draft', group: 'Render', label: 'Render a draft…', icon: <Film />, shortcut: `${MOD}↵`, run: () => set({ paletteOpen: false, exportOpen: true, exportPreset: 'draft' }) },
      { id: 'render-full', group: 'Render', label: 'Render in Full HD…', icon: <Wand2 />, run: () => set({ paletteOpen: false, exportOpen: true, exportPreset: 'full' }) },
      { id: 'theme', group: 'View', label: 'Toggle dark / light theme', icon: document.documentElement.dataset.theme === 'dark' ? <Sun /> : <Moon />, run: () => (close(), useUi.getState().setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark')) },
      { id: 'shortcuts', group: 'View', label: 'Keyboard shortcuts', icon: <Keyboard />, shortcut: '?', run: () => set({ paletteOpen: false, shortcutsOpen: true }) },
      { id: 'problems', group: 'View', label: 'Show problems', icon: <Activity />, run: () => set({ paletteOpen: false, problemsOpen: true }) },
      { id: 'panels', group: 'View', label: 'Hide or show both side panels', icon: <PanelLeft />, shortcut: `${MOD}\\`, run: () => {
          close()
          const ui = useUi.getState()
          const hide = ui.leftOpen && ui.rightOpen
          ui.set({ leftOpen: !hide, rightOpen: !hide })
        },
      },
      { id: 'left', group: 'View', label: 'Toggle the left panel', icon: <PanelLeft />, run: () => (close(), useUi.getState().set({ leftOpen: !useUi.getState().leftOpen })) },
      { id: 'right', group: 'View', label: 'Toggle the inspector', icon: <PanelRight />, run: () => (close(), useUi.getState().set({ rightOpen: !useUi.getState().rightOpen })) },
    ]
    if (spec) {
      const slots = sceneSlots(spec)
      spec.scenes.forEach((sc, i) =>
        out.push({
          id: `scene-${i}`,
          group: 'Scenes',
          label: `Scene ${i + 1}: ${sc.id}`,
          hint: `${sc.background.template} · ${sc.duration_sec.toFixed(1)} s`,
          icon: <Film />,
          run: () => {
            close()
            st().select({ kind: 'scene', scene: i })
            st().setPlayhead(slots[i].start + 0.01)
            nav(`/p/${id}`)
          },
        }),
      )
      spec.characters.forEach((c) =>
        out.push({ id: `char-${c.id}`, group: 'Cast', label: `Select ${characterName(spec, c.id)}`, hint: c.archetype, icon: <UserPlus />, run: () => (close(), st().select({ kind: 'character', id: c.id })) }),
      )
      for (const o of catalog?.objects ?? [])
        out.push({
          id: `object-${o.name}`,
          group: 'Objects',
          label: `Add object: ${objectName(o.name)}`,
          hint: [sizeText(o.size ?? (o.height === undefined ? undefined : o.height / PERSON_HEIGHT)), o.summary].filter(Boolean).join(' · '),
          keywords: [...(o.tags ?? []), o.summary, o.name].join(' '),
          searchOnly: true,
          icon: <Boxes />,
          run: () =>
            (close(),
            st().edit((d) => {
              const added = addObject(d, catalog, o.name, st().playhead)
              if (added) st().select(added)
            })),
        })
    }
    return out
  }, [spec, id, nav, set, catalog])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return commands.filter((c) => !c.searchOnly)
    return commands.filter((c) => `${c.group} ${c.label} ${c.hint ?? ''} ${c.keywords ?? ''}`.toLowerCase().includes(needle))
  }, [commands, q])

  useEffect(() => setActive(0), [q, open])
  useEffect(() => {
    if (!open) setQ('')
  }, [open])
  useEffect(() => listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' }), [active])

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') (e.preventDefault(), setActive((a) => Math.min(shown.length - 1, a + 1)))
    else if (e.key === 'ArrowUp') (e.preventDefault(), setActive((a) => Math.max(0, a - 1)))
    else if (e.key === 'Enter') (e.preventDefault(), shown[active]?.run())
  }

  let lastGroup = ''
  return (
    <RDialog.Root open={open} onOpenChange={(o) => set({ paletteOpen: o })}>
      <RDialog.Portal>
        <RDialog.Overlay className="fade fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
        <RDialog.Content aria-label="Command palette" className="pop fixed left-1/2 top-[14vh] z-50 w-[min(560px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-[14px] bg-panel shadow-[var(--shadow-pop)] outline-none" onKeyDown={onKey}>
          <RDialog.Title className="sr-only">Command palette</RDialog.Title>
          <RDialog.Description className="sr-only">Type to search commands, scenes, characters and library objects</RDialog.Description>
          <div className="flex items-center gap-2 border-b border-line px-3">
            <Search className="size-4 text-faint" aria-hidden />
            <input ref={inputRef} autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search commands, scenes, characters, objects…" aria-label="Search commands" className="h-11 flex-1 bg-transparent text-[14px] outline-none placeholder:text-faint" />
            <Kbd>esc</Kbd>
          </div>
          <div ref={listRef} role="listbox" aria-label="Commands" className="max-h-[50vh] overflow-y-auto p-1.5">
            {shown.length === 0 && <div className="px-3 py-8 text-center text-muted">Nothing matches “{q}”.</div>}
            {shown.map((c, i) => {
              const header = c.group !== lastGroup
              lastGroup = c.group
              return (
                <div key={c.id}>
                  {header && <div className="eyebrow px-2.5 pb-1 pt-2.5">{c.group}</div>}
                  <button role="option" aria-selected={i === active} data-active={i === active} onMouseMove={() => setActive(i)} onClick={c.run} className={cn('flex w-full items-center gap-3 rounded-ctl px-2.5 py-2 text-left', i === active ? 'bg-hover' : '')}>
                    <span className="text-muted [&_svg]:size-4">{c.icon}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{c.label}</span>
                      {c.hint && <span className="block truncate text-[11.5px] text-faint">{c.hint}</span>}
                    </span>
                    {c.shortcut && <Kbd>{c.shortcut}</Kbd>}
                  </button>
                </div>
              )
            })}
          </div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  )
}
