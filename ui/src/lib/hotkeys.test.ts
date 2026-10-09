import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { matches, useHotkeys } from './hotkeys'

const key = (init: KeyboardEventInit & { key: string }) => new KeyboardEvent('keydown', init)

describe('hotkey matching', () => {
  it('treats mod as Cmd or Ctrl and requires every modifier to agree', () => {
    expect(matches('mod+z', key({ key: 'z', metaKey: true }))).toBe(true)
    expect(matches('mod+z', key({ key: 'z', ctrlKey: true }))).toBe(true)
    expect(matches('mod+z', key({ key: 'z' }))).toBe(false)
    expect(matches('mod+z', key({ key: 'z', metaKey: true, shiftKey: true }))).toBe(false)
    expect(matches('mod+shift+z', key({ key: 'Z', metaKey: true, shiftKey: true }))).toBe(true)
  })

  it('knows named keys and bare keys', () => {
    expect(matches('space', key({ key: ' ' }))).toBe(true)
    expect(matches('left', key({ key: 'ArrowLeft' }))).toBe(true)
    expect(matches('shift+right', key({ key: 'ArrowRight', shiftKey: true }))).toBe(true)
    expect(matches('alt+left', key({ key: 'ArrowLeft', altKey: true }))).toBe(true)
    expect(matches('s', key({ key: 's', metaKey: true }))).toBe(false)
    expect(matches('delete', key({ key: 'Delete' }))).toBe(true)
  })

  it('finds a question mark however the keyboard makes it', () => {
    expect(matches('shift+?', key({ key: '?', shiftKey: true }))).toBe(true)
    expect(matches('?', key({ key: '?' }))).toBe(true)
    expect(matches('?', key({ key: '?', shiftKey: true }))).toBe(false)
    expect(matches('mod+c', key({ key: 'c', ctrlKey: true }))).toBe(true)
  })
})

describe('the global shortcuts', () => {
  const press = (target: EventTarget, init: KeyboardEventInit & { key: string }) => target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init }))

  it('answer a key aimed at the document as well as one aimed at an element', () => {
    const open = vi.fn()
    renderHook(() => useHotkeys({ '?': open }))
    expect(() => press(document, { key: '?' })).not.toThrow() // there is no element to ask "is this a text box?"
    press(document.body, { key: '?' })
    expect(open).toHaveBeenCalledTimes(2)
  })

  it('leave a bare key to a text box, and the clipboard keys too, but not to the page', () => {
    const bare = vi.fn()
    const paste = vi.fn()
    renderHook(() => useHotkeys({ s: bare, 'mod+v': paste }))
    const box = document.createElement('input')
    document.body.append(box)
    press(box, { key: 's' })
    press(box, { key: 'v', ctrlKey: true })
    expect(bare).not.toHaveBeenCalled()
    expect(paste).not.toHaveBeenCalled() // the box pastes into itself
    press(document.body, { key: 'v', ctrlKey: true })
    expect(paste).toHaveBeenCalledTimes(1)
    box.remove()
  })

  it('lets the browser have the key when a handler says it is not its business', () => {
    renderHook(() => useHotkeys({ 'mod+c': () => false }))
    const ev = new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, bubbles: true, cancelable: true })
    document.body.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(false)
    const claimed = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true })
    renderHook(() => useHotkeys({ 'mod+z': () => undefined }))
    document.body.dispatchEvent(claimed)
    expect(claimed.defaultPrevented).toBe(true)
  })
})
