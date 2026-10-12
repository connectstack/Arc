import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { normalizeSpec, stripDefaults } from '@/lib/normalize'
import { addScene } from '@/features/studio/ops'
import { useProject } from './project'

async function open() {
  window.history.replaceState({}, '', '/')
  const api = await createMockApi()
  useProject.getState().load(await api.getProject('story-50s'))
}

beforeEach(() => useProject.getState().unload())

describe('the selection across undo and redo', () => {
  it('lets go of something an undo removed, and the redo brings the thing back', async () => {
    await open()
    const st = useProject.getState
    const n = st().spec!.scenes.length
    st().edit((d) => st().select(addScene(d)))
    expect(st().selection).toEqual({ kind: 'scene', scene: n })
    st().undo()
    expect(st().selection).toEqual({ kind: 'reel' }) // the scene is gone, so is its selection
    st().redo()
    expect(st().spec!.scenes).toHaveLength(n + 1)
  })

  it('keeps a selection that still exists, and clears the extra clips', async () => {
    await open()
    const st = useProject.getState
    st().select({ kind: 'scene', scene: 1 })
    st().edit((d) => void (d.meta.title = 'x'))
    st().undo()
    expect(st().selection).toEqual({ kind: 'scene', scene: 1 })
    st().select({ kind: 'action', scene: 0, layer: 0, action: 0 })
    st().toggleSelect({ kind: 'action', scene: 0, layer: 0, action: 1 })
    expect(st().extra).toHaveLength(1)
    st().edit((d) => void (d.meta.title = 'y'))
    st().undo()
    expect(st().extra).toEqual([])
  })

  it('takes the main clip out of a multiple selection and hands over to the next', async () => {
    await open()
    const st = useProject.getState
    const a = { kind: 'action', scene: 0, layer: 0, action: 0 } as const
    const b = { kind: 'action', scene: 0, layer: 0, action: 1 } as const
    st().select(a)
    st().toggleSelect(b)
    st().toggleSelect(a)
    expect(st().selection).toEqual(b)
    expect(st().extra).toEqual([])
    st().toggleSelect(b) // the last one stays selected: a selection is never emptied by Shift
    expect(st().selection).toEqual(b)
    st().select({ kind: 'scene', scene: 0 })
    st().toggleSelect(a) // from a scene, Shift+click on a clip just selects it
    expect(st().selection).toEqual(a)
  })
})

describe('saving', () => {
  it('is done after one save, even when a scene made in the editor comes back from the file with its keys in another order', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    const doc = await api.getProject('story-50s')
    const st = useProject.getState
    st().load(doc)
    st().edit((d) => void addScene(d)) // a new scene: its keys are in the editor's order, the file's are not
    expect(st().save).toBe('dirty')

    const sent = { spec: st().spec!, script: st().script }
    st().setSaving()
    const saved = await api.saveProject(doc.id, stripDefaults(sent.spec) as unknown as typeof sent.spec, sent.script, st().etag!)
    // as text, what the file holds is not what the editor holds (the same keys in another order) ...
    expect(JSON.stringify(normalizeSpec(saved.spec))).not.toBe(JSON.stringify(sent.spec))
    st().markSaved(saved, sent)
    // ... and it is still saved, with what is on disk the same as what was sent
    expect(st().save).toBe('saved')
    expect(st().savedSpec).toBe(sent.spec)
    expect(st().etag).toBe(saved.etag)
  })

  it('keeps an edit made while the save was on its way dirty, a script edit too', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    const doc = await api.getProject('story-50s')
    const st = useProject.getState
    st().load(doc)
    st().edit((d) => void (d.meta.title = 'one'))
    let sent = { spec: st().spec!, script: st().script }
    st().setSaving()
    st().edit((d) => void (d.meta.title = 'two')) // in flight
    st().markSaved(doc, sent)
    expect(st().save).toBe('dirty')

    st().edit((d) => void (d.meta.title = 'three'))
    sent = { spec: st().spec!, script: st().script }
    st().setSaving()
    st().setScript('a new script') // in flight
    st().markSaved(doc, sent)
    expect(st().save).toBe('dirty')
  })

  it('without a record of what was sent, compares the saved file with the editor as before', async () => {
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    const doc = await api.getProject('story-50s')
    const st = useProject.getState
    st().load(doc)
    st().markSaved(doc)
    expect(st().save).toBe('saved')
    st().edit((d) => void (d.meta.title = 'changed'))
    st().markSaved(doc)
    expect(st().save).toBe('dirty')
  })
})
