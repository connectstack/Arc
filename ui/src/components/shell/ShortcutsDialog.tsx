import { Dialog, Kbd } from '@/components/ui'
import { SHORTCUT_GROUPS } from '@/lib/shortcuts'
import { useStudio } from '@/store/studio'

/** The `?` list: every shortcut, grouped. */
export function ShortcutsDialog() {
  const open = useStudio((s) => s.shortcutsOpen)
  const set = useStudio((s) => s.set)
  return (
    <Dialog open={open} onOpenChange={(o) => set({ shortcutsOpen: o })} size="xl" title="Keyboard shortcuts" description="Bare keys work when no text box has the focus. Press ? any time to see this list.">
      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
        {SHORTCUT_GROUPS.map((g) => (
          <section key={g.title} aria-label={g.title}>
            <h3 className="eyebrow mb-1.5">{g.title}</h3>
            <ul className="m-0 flex list-none flex-col p-0">
              {g.items.map((it) => (
                <li key={it.what} className="flex items-center justify-between gap-4 border-b border-line py-1.5 last:border-b-0">
                  <span className="text-[13px]">{it.what}</span>
                  <span className="flex shrink-0 items-center gap-1" aria-label={it.keys.join(' ')}>
                    {it.keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Dialog>
  )
}
