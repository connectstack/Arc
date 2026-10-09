import { useEffect, useRef } from 'react'

/** Returning `false` means "not mine": the key then does what the browser would have done (copying selected text, say). */
export type KeyHandler = (e: KeyboardEvent) => void | false

/** "mod+shift+z" -> does this event match? `mod` is Cmd on a Mac and Ctrl elsewhere. */
export function matches(combo: string, e: KeyboardEvent): boolean {
  const parts = combo.toLowerCase().split('+')
  const key = parts[parts.length - 1]
  const want = new Set(parts.slice(0, -1))
  const mod = e.metaKey || e.ctrlKey
  if (want.has('mod') !== mod) return false
  if (want.has('shift') !== e.shiftKey) return false
  if (want.has('alt') !== e.altKey) return false
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase()
  const aliases: Record<string, string> = { space: ' ', esc: 'escape', del: 'delete', left: 'arrowleft', right: 'arrowright', up: 'arrowup', down: 'arrowdown' }
  return k === (aliases[key] ?? key)
}

function typing(target: EventTarget | null): boolean {
  // a key event can be aimed at the document or the window (a synthetic one, or an extension's): nobody is typing there
  if (!(target instanceof HTMLElement)) return false
  const el = target
  if (el.isContentEditable) return true
  const tag = el.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const t = (el as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'range', 'color'].includes(t)
  }
  return !!el.closest('.cm-editor') // the JSON editor
}

/**
 * Global keyboard shortcuts. Bare keys are ignored while typing in a field; `mod+...` combos always fire
 * (except undo/redo and cut/copy/paste inside a text field, which belong to the field). Handlers see the newest closures.
 */
export function useHotkeys(bindings: Record<string, KeyHandler>, enabled = true): void {
  const ref = useRef(bindings)
  ref.current = bindings
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return
      const inField = typing(e.target)
      for (const [combo, fn] of Object.entries(ref.current)) {
        if (!matches(combo, e)) continue
        const hasMod = combo.toLowerCase().includes('mod+')
        // undo, redo and the clipboard keys belong to a text field while it has focus
        if (inField && (!hasMod || /\+(z|y|c|x|v|a)$/i.test(combo))) continue
        if (fn(e) !== false) e.preventDefault()
        return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}

export const isMac = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform)
export const MOD = isMac ? '⌘' : 'Ctrl'
