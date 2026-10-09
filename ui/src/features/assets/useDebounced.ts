import { useEffect, useState } from 'react'

/**
 * `value`, but only once it has stopped changing for `ms` (previews that are drawn by the server wait for the settings to settle).
 * `at` can say that a change should show at once: it is given the new value and the one now shown (the first picture of a new
 * upload has nothing to wait for).
 */
export function useDebounced<T>(value: T, ms: number, at?: (value: T, shown: T) => boolean): T {
  const [settled, setSettled] = useState(value)
  const now = at ? at(value, settled) : false
  useEffect(() => {
    if (now) {
      setSettled(value)
      return
    }
    const t = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(t)
  }, [value, ms, now])
  return now ? value : settled
}
