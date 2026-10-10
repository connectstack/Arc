// The Build panel with something in every step, checked by axe-core: every control named, headings in order, lists and groups labelled.
// (jsdom has no layout, so the colour contrast of the tokens is left to contrast.test.ts.)
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'
import { libraryApi } from '@/features/assets/testing'
import { scratchSpec } from '@/lib/spec'
import { useProject } from '@/store/project'
import { useStudio, type BuildStep } from '@/store/studio'
import { renderScreen } from '@/test/harness'
import { addActionAfter, addCameraMove, addCaptionAfter, addCharacterToScene, addObject, addSfx } from '../ops'
import { BuildPanel } from './BuildPanel'

async function violations(label: string): Promise<string[]> {
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
  return result.violations.map((v) => `${label}: ${v.id} (${v.help}): ${v.nodes.slice(0, 3).map((n) => n.html.slice(0, 160)).join(' | ')}`)
}

async function openPanel(step: BuildStep | null) {
  const api = await libraryApi()
  useProject.getState().load(await api.createProject({ spec: scratchSpec('Café', 'paper_cutout'), title: 'Café' }))
  const catalog = await api.catalog()
  useProject.getState().edit((d) => {
    addCharacterToScene(d, 0, 'cat')
    addActionAfter(d, catalog, 0, 'cat', 'walk')
    addObject(d, catalog, 'cake', 0, 0)
    addSfx(d, 'applause', 0, 0)
    addCaptionAfter(d, catalog, 0, 'A slice of cake, please', 'cat')
    addCameraMove(d, 'zoom', 0, 0)
  })
  useProject.getState().select({ kind: 'scene', scene: 0 })
  useStudio.getState().set({ buildStep: step })
  await renderScreen(<BuildPanel />, { api })
  await screen.findByRole('heading', { name: /scene 1 of 1/i })
}

describe('accessibility of the Build panel', () => {
  for (const step of ['background', 'cast', 'objects', 'actions', 'sounds', 'words', 'camera'] as const) {
    it(`has no violations with the ${step} step open`, async () => {
      await openPanel(step)
      await screen.findByRole('region', { name: new RegExp(`^${step === 'cast' ? 'characters' : step}`, 'i') })
      expect(await violations(step)).toEqual([])
    })
  }

  it('has no violations with every step closed, and with the search of a step in use', async () => {
    const user = userEvent.setup()
    await openPanel(null)
    expect(await violations('closed')).toEqual([])
    await user.click(screen.getByRole('button', { name: /^Actions/ }))
    await user.type(await screen.findByRole('textbox', { name: 'Find an action' }), 'zzzz')
    expect(await screen.findByText(/No action matches/)).toBeInTheDocument()
    expect(await violations('no match')).toEqual([])
  })
})
