import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
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
