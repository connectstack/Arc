import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { AssetSettings, TagInput } from './AssetSettings'
import { MAX_WORDS, formFromDraft, type AssetFormValue } from './assetFields'

function Words({ initial = [] }: { initial?: string[] }) {
  const [words, setWords] = useState(initial)
  return (
    <>
      <label htmlFor="words">Words</label>
      <TagInput id="words" value={words} onChange={setWords} placeholder="dragon, wyvern" />
      <output aria-label="kept">{JSON.stringify(words)}</output>
    </>
  )
}

const kept = (): string[] => JSON.parse(screen.getByLabelText('kept').textContent ?? '[]')

describe('the words input', () => {
  it('turns a word into a chip on Enter or a comma, in any script', async () => {
    const user = userEvent.setup()
    render(<Words />)
    const box = screen.getByLabelText('Words')
    await user.type(box, 'dragon{Enter}')
    await user.type(box, 'ड्रैगन, wyvern,')
    expect(kept()).toEqual(['dragon', 'ड्रैगन', 'wyvern'])
    expect(screen.getByRole('button', { name: 'Remove ड्रैगन' })).toBeInTheDocument()
    expect(box).toHaveValue('')
  })

  it('keeps a word one way: trimmed, in lower case, without repeats', async () => {
    const user = userEvent.setup()
    render(<Words initial={['dragon']} />)
    const box = screen.getByLabelText('Words')
    await user.type(box, '  Dragon  {Enter}')
    await user.type(box, 'Fire   Dragon{Enter}')
    await user.type(box, ' ,{Enter}')
    expect(kept()).toEqual(['dragon', 'fire dragon'])
  })

  it('splits a pasted list into words', async () => {
    const user = userEvent.setup()
    render(<Words />)
    await user.click(screen.getByLabelText('Words'))
    await user.paste('cat, kitten; बिल्ली\nkitty')
    expect(kept()).toEqual(['cat', 'kitten', 'बिल्ली', 'kitty'])
  })

  it('takes the last word back with Backspace on an empty box, and any word with its button', async () => {
    const user = userEvent.setup()
    render(<Words initial={['one', 'two', 'three']} />)
    const box = screen.getByLabelText('Words')
    await user.click(box)
    await user.keyboard('{Backspace}')
    expect(kept()).toEqual(['one', 'two'])
    await user.type(box, 'x')
    await user.keyboard('{Backspace}') // deletes the letter, not a word
    expect(kept()).toEqual(['one', 'two'])
    await user.click(screen.getByRole('button', { name: 'Remove one' }))
    expect(kept()).toEqual(['two'])
    expect(box).toHaveFocus()
  })

  it('keeps a word that was still being typed when you leave the box', async () => {
    const user = userEvent.setup()
    render(
      <>
        <Words />
        <button>elsewhere</button>
      </>,
    )
    await user.type(screen.getByLabelText('Words'), 'pending')
    await user.click(screen.getByRole('button', { name: 'elsewhere' }))
    expect(kept()).toEqual(['pending'])
  })

  it('stops at the most words an asset keeps', async () => {
    const user = userEvent.setup()
    render(<Words initial={Array.from({ length: MAX_WORDS }, (_, i) => `w${i}`)} />)
    expect(screen.getByLabelText('Words')).toBeDisabled()
    expect(kept()).toHaveLength(MAX_WORDS)
    await user.click(screen.getByRole('button', { name: 'Remove w0' }))
    expect(screen.getByLabelText('Words')).toBeEnabled()
  })
})

function Form({ initial, picture = false }: { initial?: Partial<AssetFormValue>; picture?: boolean }) {
  const [value, setValue] = useState<AssetFormValue>({
    ...formFromDraft({ id: 'x', filename: 'x.svg', format: 'svg', bytes: 1, suggested: { name: 'thing', kind: 'object', summary: '', tags: [], height: 300, anchor: [0.5, 1], facing: 'right' }, notes: [], roles: [], aspect: 1 }),
    ...initial,
  })
  return <AssetSettings value={value} onChange={setValue} picture={picture} />
}

describe('the asset settings', () => {
  it('fixes a name as it is typed: lower case, underscores, a letter first', async () => {
    const user = userEvent.setup()
    render(<Form />)
    const name = screen.getByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'Fire Dragon!')
    expect(name).toHaveValue('fire_dragon_')
    await user.clear(name)
    await user.type(name, '3 dogs')
    expect(name).toHaveValue('a_3_dogs')
    await user.clear(name)
    expect(screen.getByRole('alert')).toHaveTextContent(/give it a name/i)
    expect(name).toHaveAttribute('aria-invalid', 'true')
  })

  it('moves the size with the kind until somebody sets it', async () => {
    const user = userEvent.setup()
    render(<Form />)
    const size = () => screen.getByRole('textbox', { name: 'Size in pixels value' })
    expect(size()).toHaveValue('300')
    await user.click(screen.getByRole('radio', { name: 'Character' }))
    expect(size()).toHaveValue('420')
    await user.click(screen.getByRole('button', { name: /person/ }))
    expect(size()).toHaveValue('575')
    await user.click(screen.getByRole('radio', { name: 'Object' }))
    expect(size()).toHaveValue('575') // it was set by hand: the kind leaves it alone
  })

  it('has no size, anchor or facing for a place, but says where characters stand', async () => {
    const user = userEvent.setup()
    render(<Form />)
    await user.click(screen.getByRole('radio', { name: 'Place' }))
    expect(screen.queryByRole('radiogroup', { name: 'Facing' })).not.toBeInTheDocument()
    expect(screen.queryByRole('radiogroup', { name: 'Anchor' })).not.toBeInTheDocument()
    expect(screen.queryByRole('slider', { name: 'Size in pixels' })).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Where characters stand' })).toBeInTheDocument()
  })

  it('offers to keep or remove the plain background of a picture only', () => {
    const { unmount } = render(<Form />)
    expect(screen.queryByRole('radiogroup', { name: 'Remove plain background' })).not.toBeInTheDocument()
    unmount()
    render(<Form picture />)
    expect(screen.getByRole('radiogroup', { name: 'Remove plain background' })).toBeInTheDocument()
  })
})
