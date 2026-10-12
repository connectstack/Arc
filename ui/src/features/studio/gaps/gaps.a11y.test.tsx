// The warnings about what the library cannot draw, checked by axe-core: the scene's section, the list in the Script tab and the top-bar chip.
// (jsdom has no layout, so the colour contrast of the tokens is left to contrast.test.ts.)
import { screen } from '@testing-library/react'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'
import type { LibraryGap } from '@/api/types'
import { libraryApi } from '@/features/assets/testing'
import { useProject } from '@/store/project'
import { useStudio } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { Inspector } from '../inspector/Inspector'
import { LeftPanel } from '../LeftPanel'
import { MissingChip } from './MissingAssets'
import { useIgnored } from './ignored'

async function violations(label: string): Promise<string[]> {
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
  return result.violations.map((v) => `${label}: ${v.id} (${v.help}): ${v.nodes.slice(0, 3).map((n) => n.html.slice(0, 160)).join(' | ')}`)
}

const gaps: LibraryGap[] = [
  { kind: 'object', name: 'castle', scenes: ['rainy_street', 'pip_panics'] },
  { kind: 'object', name: 'car', scenes: ['rainy_street'] }, // the library has it now
  { kind: 'place', name: 'barn', scenes: ['rainy_street'], stand_in: 'forest' },
  { kind: 'character', name: 'astronaut', scenes: ['rainy_street'], character: 'mia', stand_in: 'everyman' },
]

async function open() {
  window.localStorage.clear()
  useIgnored.setState({ lists: {} })
  const api = await libraryApi()
  useProject.getState().load(await api.getProject('story-50s'))
  useProject.getState().edit((d) => void (d.meta.library_gaps = gaps))
  return api
}

describe('accessibility of the missing-asset warnings', () => {
  it('has no violations in a scene’s inspector', async () => {
    const api = await open()
    useProject.getState().select({ kind: 'scene', scene: 0 })
    await renderScreen(<Inspector />, { api })
    await screen.findByRole('list', { name: 'Missing from the library' }, { timeout: 4000 })
    expect(await violations('scene inspector')).toEqual([])
  })

  it('has no violations in the Script tab and the scene list', async () => {
    const api = await open()
    useProject.getState().select({ kind: 'reel' })
    useStudio.setState({ leftTab: 'script' })
    const { unmount } = await renderScreen(<LeftPanel />, { api })
    await screen.findByRole('heading', { name: /things missing from the library/i }, { timeout: 4000 })
    expect(await violations('script tab')).toEqual([])
    unmount()
    useStudio.setState({ leftTab: 'scenes' })
    await renderScreen(<LeftPanel />, { api })
    await screen.findAllByRole('button', { name: /missing from the library/i }, { timeout: 4000 })
    expect(await violations('scene list')).toEqual([])
  })

  it('has no violations in the top-bar chip', async () => {
    const api = await open()
    await renderScreen(<MissingChip />, { api })
    await screen.findByRole('button', { name: /missing from the library/i }, { timeout: 4000 })
    expect(await violations('chip')).toEqual([])
  })
})
