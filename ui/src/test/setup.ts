import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// findBy* and waitFor give a slow machine a few seconds before they say no
configure({ asyncUtilTimeout: 4000 })

// jsdom has no layout engine and no media: just enough of the browser for the app to mount
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    // a desktop window: wide enough for docked panels, not a phone
    matches: /min-width/.test(query),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
})
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub)
Element.prototype.scrollIntoView = () => undefined
// jsdom has no pointer capture (dragging a clip takes it)
Element.prototype.setPointerCapture = () => undefined
Element.prototype.releasePointerCapture = () => undefined
Element.prototype.hasPointerCapture = () => false
URL.createObjectURL = () => 'blob:test'
URL.revokeObjectURL = () => undefined
HTMLMediaElement.prototype.play = () => Promise.resolve()
HTMLMediaElement.prototype.pause = () => undefined
HTMLMediaElement.prototype.load = () => undefined

afterEach(() => {
  cleanup()
  localStorage.clear()
})
