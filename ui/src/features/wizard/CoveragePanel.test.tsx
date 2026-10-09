import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Coverage, LibraryGap } from '@/api/types'
import { svgFile } from '@/features/assets/testing'
import { renderScreen } from '@/test/harness'
import { CoveragePanel, initialFor } from './CoveragePanel'
import { GapsCard } from './GapsCard'

const COVERAGE: Coverage = {
  covered: [
    { asset: 'tree', kind: 'object', source: 'builtin', words: ['tree'], count: 2 },
    { asset: 'cow', kind: 'character', source: 'user', words: ['गाय'], count: 1 },
  ],
  missing: [{ name: 'castle', kind: 'place', words: ['castle'], count: 1, snippet: 'A dragon guards the old castle', at: [[23, 29]], tags: ['fort'] }],
}

describe('the Library check under the script', () => {
  it('lists what can be drawn and what is missing, each missing thing with a way to add it', async () => {
    await renderScreen(<CoveragePanel coverage={COVERAGE} loading={false} />)
    expect(screen.getByRole('heading', { name: /library check/i })).toBeInTheDocument()
    expect(screen.getByText('tree')).toBeInTheDocument()
    expect(screen.getByText('×2')).toBeInTheDocument()
    expect(screen.getByText(/1 thing the script mentions is not in the library/i)).toBeInTheDocument()
    expect(screen.getByText(/A dragon guards the old castle/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /add castle to the library/i })).toBeEnabled()
  })

  it('says so when everything can be drawn, and shows nothing when the script names nothing', async () => {
    const { rerender, container } = await renderScreen(<CoveragePanel coverage={{ covered: COVERAGE.covered, missing: [] }} loading={false} />)
    expect(screen.getByText(/everything the script mentions can be drawn/i)).toBeInTheDocument()
    rerender(<CoveragePanel coverage={{ covered: [], missing: [] }} loading={false} />)
    expect(container.querySelector('section')).toBeNull()
  })

  it('opens the add-asset form with the name, kind and the words the script used', async () => {
    const user = userEvent.setup()
    await renderScreen(<CoveragePanel coverage={COVERAGE} loading={false} />)
    await user.click(screen.getByRole('button', { name: /add castle to the library/i }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByDisplayValue('castle')).toBeInTheDocument()
  })

  it('pre-fills what the script says about a missing thing', () => {
    const m = COVERAGE.missing[0]
    expect(initialFor(m)).toMatchObject({ name: 'castle', kind: 'place', tags: ['fort'] })
    expect(initialFor(m).hint).toContain('A dragon guards the old castle')
    expect(initialFor({ ...m, snippet: '' }).hint).toContain('“castle”')
  })
})

describe('the gaps a plan reports', () => {
  const gaps: LibraryGap[] = [
    { kind: 'place', name: 'castle', scenes: ['s02', 's03'], stand_in: 'forest' },
    { kind: 'object', name: 'sword', scenes: ['s03'] },
  ]

  it('says what stands in for each gap, or that it was left out, and in which scenes', async () => {
    await renderScreen(<GapsCard gaps={gaps} sceneNumbers={{ s02: 2, s03: 3 }} onAdded={vi.fn()} />)
    expect(screen.getByRole('heading', { name: /not in the library/i })).toBeInTheDocument()
    expect(screen.getByText('castle').closest('li')).toHaveTextContent('place · shown as forest · scenes 2, 3')
    expect(screen.getByText('sword').closest('li')).toHaveTextContent('object · left out · scene 3')
    expect(screen.getByRole('button', { name: /add sword to the library/i })).toBeEnabled()
  })

  it('starts the add form from the words the script check found for the same thing', async () => {
    const user = userEvent.setup()
    await renderScreen(
      <GapsCard
        gaps={[{ kind: 'place', name: 'castle', scenes: ['s02'], stand_in: 'forest' }]}
        known={[{ name: 'castle', kind: 'place', words: ['castle'], count: 1, snippet: 'A dragon guards the old castle', at: [[23, 29]], tags: ['fort', 'किला'] }]}
        onAdded={vi.fn()}
      />,
    )
    await user.click(screen.getByRole('button', { name: /add castle to the library/i }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/forest stands in for it/i)).toBeInTheDocument()
    await user.upload(dialog.querySelector('input[type="file"]') as HTMLInputElement, svgFile('castle.svg'))
    expect(await within(dialog).findByText('fort')).toBeInTheDocument() // the words the check found, ready to keep
    expect(within(dialog).getByText('किला')).toBeInTheDocument()
  })

  it('renders nothing when the plan lacked nothing', async () => {
    const { container } = await renderScreen(<GapsCard gaps={[]} onAdded={vi.fn()} />)
    expect(container.querySelector('section')).toBeNull()
  })
})
