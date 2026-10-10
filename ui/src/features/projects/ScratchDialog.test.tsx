import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { normalizeSpec } from '@/lib/normalize'
import { useStudio } from '@/store/studio'
import { useUi } from '@/store/ui'
import { renderScreen } from '@/test/harness'
import { ProjectsPage } from './ProjectsPage'
import { ScratchDialog } from './ScratchDialog'

beforeEach(() => {
  useUi.setState({ defaultStyle: 'paper_cutout', leftOpen: false })
  useStudio.getState().set({ leftTab: 'scenes', buildStep: null, drawer: null })
})

/** The newest project of the mock engine, as the studio would load it. */
async function newest(api: Awaited<ReturnType<typeof createMockApi>>) {
  const list = await api.listProjects()
  return api.getProject(list[0].id)
}

describe('starting a reel from scratch', () => {
  async function open() {
    const api = await createMockApi()
    await renderScreen(<ScratchDialog open onOpenChange={() => undefined} />, { api, route: '/', path: '/' })
    return { api, user: userEvent.setup(), dialog: await screen.findByRole('dialog', { name: /build a reel from scratch/i }) }
  }

  it('asks for a title and a look, and offers the three looks of the engine', async () => {
    const { dialog } = await open()
    expect(within(dialog).getByRole('textbox', { name: /title/i })).toBeInTheDocument()
    const looks = within(dialog).getByRole('radiogroup', { name: 'Visual style' })
    await waitFor(() => expect(within(looks).getAllByRole('radio')).toHaveLength(3))
    // the look chosen in the settings is the one that is ready
    expect(within(looks).getByRole('radio', { name: /paper cutout/i })).toHaveAttribute('aria-checked', 'true')
  })

  it('creates an empty reel in the look chosen, opens the studio on the Build panel and shows the left panel', async () => {
    const { api, user, dialog } = await open()
    await user.type(within(dialog).getByRole('textbox', { name: /title/i }), 'My café')
    await user.click(await within(dialog).findByRole('radio', { name: /stickman/i }))
    await user.click(within(dialog).getByRole('button', { name: /start building/i }))

    expect(await screen.findByTestId('elsewhere')).toBeInTheDocument() // the studio route
    const doc = await newest(api)
    const spec = normalizeSpec(doc.spec)
    expect(spec.meta).toMatchObject({ title: 'My café', style: 'stickman' })
    expect(spec.characters).toEqual([])
    expect(spec.scenes).toHaveLength(1)
    expect(spec.scenes[0]).toMatchObject({ layers: [], objects: [], captions: [], sfx: [] })
    expect(doc.script).toBe('')
    expect(useStudio.getState()).toMatchObject({ leftTab: 'build', buildStep: 'background', drawer: null })
    expect(useUi.getState().leftOpen).toBe(true)
  })

  it('calls an untitled reel "Untitled reel", and starts when Enter is pressed in the title', async () => {
    const { api, user, dialog } = await open()
    await user.type(within(dialog).getByRole('textbox', { name: /title/i }), '{Enter}')
    expect(await screen.findByTestId('elsewhere')).toBeInTheDocument()
    expect(normalizeSpec((await newest(api)).spec).meta.title).toBe('Untitled reel')
  })

  it('uses the look the person has set as their default', async () => {
    useUi.setState({ defaultStyle: 'flat_vector' })
    const { api, user, dialog } = await open()
    await waitFor(() => expect(within(dialog).getByRole('radio', { name: /flat vector/i })).toHaveAttribute('aria-checked', 'true'))
    await user.click(within(dialog).getByRole('button', { name: /start building/i }))
    await screen.findByTestId('elsewhere')
    expect(normalizeSpec((await newest(api)).spec).meta.style).toBe('flat_vector')
  })
})

describe('the way to it from the projects page', () => {
  it('has a card for it beside the other ways to start, which opens the dialog', async () => {
    const user = userEvent.setup()
    await renderScreen(<ProjectsPage />, { route: '/', path: '/' })
    await user.click(await screen.findByRole('button', { name: /build from scratch/i }))
    expect(await screen.findByRole('dialog', { name: /build a reel from scratch/i })).toBeInTheDocument()
  })
})
