import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { createMockApi } from '@/api/mock'
import { renderScreen } from '@/test/harness'
import { ObjectPicker, matchObjects, sizeText } from './ObjectPicker'
import { OBJECT_FIXTURES, withObjects } from './objects.fixture'

const names = (q: string) => matchObjects(OBJECT_FIXTURES, q).map((e) => e.name)

describe('finding an object in the library', () => {
  it('lists everything for an empty search, in the library’s order', () => {
    expect(names('')).toEqual(['car', 'tree', 'cake', 'billboard', 'balloon'])
    expect(names('   ')).toEqual(['car', 'tree', 'cake', 'billboard', 'balloon'])
  })

  it('matches the name, the words a script may use (any language) and what the summary says', () => {
    expect(names('car')).toEqual(['car'])
    expect(names('गाड़ी')).toEqual(['car']) // Hindi tag
    expect(names('पेड़')).toEqual(['tree'])
    expect(names('birthday')).toEqual(['cake']) // a tag
    expect(names('posts')).toEqual(['billboard']) // only in the summary
    expect(names('zzz')).toEqual([])
  })

  it('wants every word to match, and puts the best match first', () => {
    expect(names('red car')).toEqual(['car'])
    expect(names('red tree')).toEqual([])
    expect(names('ba')).toEqual(['balloon', 'car']) // a name that starts with it beats a summary that only contains it ("hatchback")
    expect(names('BALLOON')).toEqual(['balloon'])
  })

  it('says how tall a thing is in persons', () => {
    expect(sizeText(1)).toBe('1× a person')
    expect(sizeText(0.52)).toBe('0.52× a person')
    expect(sizeText(1.08)).toBe('1.1× a person')
    expect(sizeText(12.4)).toBe('12× a person')
    expect(sizeText(undefined)).toBe('')
  })
})

describe('the picker', () => {
  async function open(props: Partial<Parameters<typeof ObjectPicker>[0]> = {}) {
    const onPick = vi.fn()
    const api = withObjects(await createMockApi())
    await renderScreen(<ObjectPicker onPick={onPick} {...props} />, { api })
    await screen.findByRole('button', { name: /car/i })
    return { onPick }
  }

  it('shows each object with its size and what it is', async () => {
    await open()
    const list = screen.getByRole('list', { name: 'Library objects' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(5)
    const car = within(list).getByRole('button', { name: /car/i })
    expect(car).toHaveTextContent('0.52× a person')
    expect(car).toHaveTextContent('A small red hatchback')
  })

  it('searches as you type, in Hindi too, and picks the first match with Enter', async () => {
    const user = userEvent.setup()
    const { onPick } = await open()
    await user.type(screen.getByRole('textbox', { name: 'Find an object' }), 'गुब्बारा')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('1 object matches')
    await user.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith('balloon')
  })

  it('moves through the list with the arrow keys and picks with a click or Enter', async () => {
    const user = userEvent.setup()
    const { onPick } = await open()
    await user.click(screen.getByRole('textbox', { name: 'Find an object' }))
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('button', { name: /car/i })).toHaveFocus()
    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(screen.getByRole('button', { name: /cake/i })).toHaveFocus()
    await user.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}')
    expect(screen.getByRole('textbox', { name: 'Find an object' })).toHaveFocus()
    await user.click(screen.getByRole('button', { name: /tree/i }))
    expect(onPick).toHaveBeenCalledWith('tree')
  })

  it('says when nothing matches and offers to add your own', async () => {
    const user = userEvent.setup()
    const onAddOwn = vi.fn()
    await open({ onAddOwn })
    await user.type(screen.getByRole('textbox', { name: 'Find an object' }), 'dragon')
    expect(screen.getByText(/no object matches “dragon”/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /add your own/i }))
    expect(onAddOwn).toHaveBeenCalled()
  })

  it('marks the object that is chosen now', async () => {
    await open({ current: 'tree' })
    expect(screen.getByRole('button', { name: /tree/i })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /car/i })).not.toHaveAttribute('aria-current')
  })
})
