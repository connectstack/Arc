// "Build from scratch": a name and a look, then an empty reel opens in the studio with the Build panel ready. No script, no planner:
// the backgrounds, characters, objects, actions and sounds are all picked from the library by hand.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Hammer } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useApi } from '@/api/context'
import { useCatalog } from '@/api/hooks'
import type { ReelSpec } from '@/api/types'
import { ApiError } from '@/api/types'
import { Button, Dialog, Field, Input, toast } from '@/components/ui'
import { cn } from '@/lib/cn'
import { titleCase } from '@/lib/format'
import { stripDefaults } from '@/lib/normalize'
import { scratchSpec } from '@/lib/spec'
import { useWide } from '@/lib/useMedia'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'

export function ScratchDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const api = useApi()
  const nav = useNavigate()
  const qc = useQueryClient()
  const wide = useWide()
  const styles = useCatalog().data?.styles ?? []
  const defaultStyle = useUi((s) => s.defaultStyle)
  const [title, setTitle] = useState('')
  const [pick, setPick] = useState<string>()
  const style = pick ?? (styles.some((s) => s.name === defaultStyle) ? defaultStyle : (styles[0]?.name ?? defaultStyle))

  const create = useMutation({
    mutationFn: () => {
      const name = title.trim() || 'Untitled reel'
      return api.createProject({ spec: stripDefaults(scratchSpec(name, style)) as unknown as ReelSpec, title: name })
    },
    onSuccess: (doc) => {
      void qc.invalidateQueries({ queryKey: ['projects'] })
      // the studio opens on the Build panel, with the left panel showing (a drawer on a narrow window)
      useStudio.getState().set({ leftTab: 'build', buildStep: 'background', drawer: wide ? null : 'left' })
      useUi.getState().set({ leftOpen: true })
      onOpenChange(false)
      nav(`/p/${doc.id}`)
    },
    onError: (e) => toast.error('Could not create the project', e instanceof ApiError ? `${e.detail}${e.hint ? ` — ${e.hint}` : ''}` : String(e)),
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Build a reel from scratch"
      description="Start with an empty scene and put it together yourself: backgrounds, characters, objects, actions, sounds and words, all picked from the library."
      size="md"
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={create.isPending} onClick={() => create.mutate()}>
            <Hammer className="size-4" aria-hidden /> Start building
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <Field label="Title" hint="Optional: you can rename the reel any time" htmlFor="scratch-title">
          <Input id="scratch-title" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && !create.isPending && create.mutate()} placeholder="Untitled reel" />
        </Field>
        <div>
          <div className="mb-1.5 text-[12px] font-medium">Look</div>
          <div className="grid grid-cols-3 gap-3" role="radiogroup" aria-label="Visual style">
            {styles.map((s) => (
              <button key={s.name} type="button" role="radio" aria-checked={style === s.name} onClick={() => setPick(s.name)} title={s.summary} className={cn('relative overflow-hidden rounded-card border-2 text-left transition-colors', style === s.name ? 'border-accent' : 'border-line hover:border-line-strong')}>
                <img src={api.libraryThumbUrl('style', s.name, 'day')} alt="" loading="lazy" className="aspect-[9/12] w-full object-cover" />
                {style === s.name && (
                  <span className="absolute right-1.5 top-1.5 grid size-5 place-items-center rounded-full bg-solid text-accent-fg">
                    <Check className="size-3" aria-hidden />
                  </span>
                )}
                <span className="block truncate bg-panel px-2 py-1.5 text-[12px] font-semibold">{titleCase(s.name)}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  )
}
