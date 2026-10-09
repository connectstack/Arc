// The studio with objects in it, checked by axe-core: every control named, headings in order, menus and dialogs labelled, no nested controls.
// (jsdom has no layout, so the colour contrast of the tokens is left to contrast.test.ts.)
import { act, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axe from 'axe-core'
import { describe, expect, it } from 'vitest'
import { createMockApi } from '@/api/mock'
import { newObject } from '@/lib/spec'
import { useProject } from '@/store/project'
import { renderScreen } from '@/test/harness'
import { objectName } from './objects'
import { withObjects } from './objects.fixture'
import { StudioPage } from './StudioPage'

async function violations(label: string): Promise<string[]> {
  const result = await axe.run(document.body, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
  return result.violations.map((v) => `${label}: ${v.id} (${v.help}): ${v.nodes.slice(0, 3).map((n) => n.html.slice(0, 160)).join(' | ')}`)
}

async function openStudio() {
  window.history.replaceState({}, '', '/')
  const api = withObjects(await createMockApi())
  useProject.getState().load(await api.getProject('story-50s'))
  useProject.getState().edit((d) => {
    d.scenes[0].objects.push({ ...newObject('car', [0.2, 0.74], 0.675), t0: 1, motions: [{ type: 'hop', t0: 1, t1: 2, ease: 'ease_in_out' }, { type: 'move', t0: 3, t1: 5, to: 'right', ease: 'ease_in_out' }] })
    d.scenes[0].objects.push(newObject('tree', 'left'))
  })
  await renderScreen(<StudioPage />, { api })
  await screen.findByRole('region', { name: 'Objects in scene 1' })
}

describe('accessibility of the objects in the studio', () => {
  it('has no violations with the scene selected: the outline, the lanes and the scene’s list of objects', async () => {
    await openStudio()
    act(() => useProject.getState().select({ kind: 'scene', scene: 0 }))
    await screen.findByRole('button', { name: 'Add an object to this scene' })
    expect(await violations('scene')).toEqual([])
  })

  it('has no violations with an object selected, its motions listed and one of them open', async () => {
    const user = userEvent.setup()
    await openStudio()
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    await screen.findByRole('heading', { name: `Scene 1 › ${objectName('car')}` })
    expect(await violations('object')).toEqual([])
    await user.click(await screen.findByRole('button', { name: /^hop/i, expanded: false }))
    expect(await violations('motion open')).toEqual([])
  })

  it('has no violations with a motion selected', async () => {
    await openStudio()
    act(() => useProject.getState().select({ kind: 'motion', scene: 0, object: 0, motion: 1 }))
    await screen.findByRole('heading', { name: 'Scene 1 › car › move' })
    expect(await violations('motion')).toEqual([])
  })

  it('has no violations with the picker, the motion menus and a right-click menu open', async () => {
    const user = userEvent.setup()
    await openStudio()
    act(() => useProject.getState().select({ kind: 'object', scene: 0, object: 0 }))
    await screen.findByRole('heading', { name: 'Scene 1 › car' })
    await user.click(screen.getByRole('button', { name: 'Add object' }))
    expect(await screen.findByRole('dialog', { name: 'Choose an object' })).toBeInTheDocument()
    expect(await violations('picker')).toEqual([])
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Add a motion' }))
    await screen.findAllByRole('menuitem')
    expect(await violations('inspector menu')).toEqual([])
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Add a motion for car at the playhead' }))
    await screen.findAllByRole('menuitem')
    expect(await violations('lane menu')).toEqual([])
    await user.keyboard('{Escape}')
    await user.pointer({ keys: '[MouseRight]', target: document.querySelector('[data-clip="ob-0-1"]') as Element })
    await screen.findByRole('menuitem', { name: /^copy/i })
    expect(await violations('clip menu')).toEqual([])
  })
})
