import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { ConflictDialog } from './ProjectLayout'

beforeEach(() => useProject.getState().unload())

describe('a project changed on disk while it was open', () => {
  it('says what each side did, and loading the disk version drops the local edits', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    const doc = await api.getProject('story-50s')
    useProject.getState().load(doc)
    const st = useProject.getState
    st().edit((d) => void (d.meta.title = 'My edit'))
    const onDisk = structuredClone(doc)
    onDisk.spec.meta.title = 'Edited elsewhere'
    onDisk.spec.scenes[1].duration_sec += 1
    onDisk.etag = 'new-etag'
    act(() => st().setConflict(onDisk))
    await renderScreen(<ConflictDialog />, { api })

    const dialog = await screen.findByRole('dialog', { name: /changed on disk/i })
    expect(dialog).toHaveTextContent(/on disk\s*Renamed the reel to “Edited elsewhere”; Scene 2: length 5.9 → 6.9 s/i)
    expect(dialog).toHaveTextContent(/your edits\s*Renamed the reel to “My edit”/i)

    await user.click(screen.getByRole('button', { name: /load the disk version/i }))
    expect(st().spec!.meta.title).toBe('Edited elsewhere')
    expect(st().conflict).toBeNull()
    expect(st().past).toHaveLength(0)
  })

  it('keeps my edits and takes the new etag when I choose to overwrite', async () => {
    const user = userEvent.setup()
    window.history.replaceState({}, '', '/')
    const api = await createMockApi()
    const doc = await api.getProject('story-50s')
    useProject.getState().load(doc)
    const st = useProject.getState
    st().edit((d) => void (d.meta.title = 'Mine'))
    act(() => st().setConflict({ ...structuredClone(doc), etag: 'newer' }))
    await renderScreen(<ConflictDialog />, { api })
    await user.click(await screen.findByRole('button', { name: /keep my edits and overwrite/i }))
    expect(st().spec!.meta.title).toBe('Mine')
    expect(st().etag).toBe('newer')
    expect(st().save).toBe('dirty')
  })
})
