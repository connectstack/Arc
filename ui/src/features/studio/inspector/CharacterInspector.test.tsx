import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { libraryApi } from '@/features/assets/testing'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { Inspector } from './Inspector'

async function open(archetype?: string) {
  const api = await libraryApi()
  useProject.getState().load(await api.getProject('story-50s'))
  if (archetype) useProject.getState().edit((d) => void (d.characters.find((c) => c.id === 'mia')!.archetype = archetype))
  useProject.getState().select({ kind: 'character', id: 'mia' })
  return api
}
const mia = () => useProject.getState().spec!.characters.find((c) => c.id === 'mia')!

beforeEach(() => useProject.getState().unload())

describe('the colours and props of a character', () => {
  it('are the engine body’s: every role of the palette, and the props it can hold', async () => {
    await renderScreen(<Inspector />, { api: await open() })
    expect(await screen.findByText('Skin')).toBeInTheDocument()
    expect(screen.getByText('Shirt')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'umbrella' })).toBeInTheDocument()
  })

  it('are only the marked parts of a picture from the library, and a picture holds no props', async () => {
    await renderScreen(<Inspector />, { api: await open('cat') })
    expect(await screen.findByText('Fur')).toBeInTheDocument()
    expect(screen.getByText('Belly')).toBeInTheDocument()
    expect(screen.queryByText('Skin')).not.toBeInTheDocument()
    expect(screen.queryByText('Shirt')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'umbrella' })).not.toBeInTheDocument()
    expect(screen.getByText(/does not hold props/i)).toBeInTheDocument()
  })

  it('recolour a marked part of a picture, and reset it', async () => {
    const user = userEvent.setup()
    await renderScreen(<Inspector />, { api: await open('cat') })
    const field = await screen.findByRole('textbox', { name: /fur/i })
    await user.clear(field)
    await user.type(field, '#2a6fdb')
    await user.tab()
    expect(mia().palette).toMatchObject({ fur: '#2a6fdb' })
  })

  it('drop what no longer applies when a body is swapped for a picture', async () => {
    const user = userEvent.setup()
    await renderScreen(<Inspector />, { api: await open() })
    expect(mia().props).toEqual(['backpack'])
    expect(mia().palette).toMatchObject({ shirt: '#2a9d8f', hair: '#d9822b' })
    await user.click(await screen.findByRole('button', { name: 'cow' }))
    expect(mia().archetype).toBe('cow')
    expect(mia().props).toEqual([])
    expect(mia().palette).toEqual({}) // the cow's marked part is "coat": neither shirt nor hair survive
    expect(await screen.findByText('Coat')).toBeInTheDocument()
  })

  it('keep what a body can still use when it is swapped for another body', async () => {
    const user = userEvent.setup()
    await renderScreen(<Inspector />, { api: await open() })
    await user.click(await screen.findByRole('button', { name: 'hero' }))
    expect(mia().archetype).toBe('hero')
    expect(mia().props).toEqual(['backpack'])
    expect(mia().palette).toMatchObject({ shirt: '#2a9d8f' })
  })
})
