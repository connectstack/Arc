import { useEffect } from 'react'

/**
 * While an asset dialog is open, keys typed in it belong to it. The studio listens for bare keys on the window (Delete removes the
 * selected clip, S splits, Space plays), and a dialog's buttons are not text fields, so without this a key meant for the dialog
 * could edit the reel behind it. Escape is Radix's (it listens before this does) and Tab stays with the focus trap.
 */
export function useModalKeys(): void {
  useEffect(() => {
    const stop = (e: KeyboardEvent) => {
      // a dialog, and the list a select inside it opens (that one is drawn outside the dialog)
      if (e.target instanceof Element && e.target.closest('[role="dialog"], [role="listbox"]')) e.stopPropagation()
    }
    document.addEventListener('keydown', stop)
    return () => document.removeEventListener('keydown', stop)
  }, [])
}
