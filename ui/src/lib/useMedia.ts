import { useSyncExternalStore } from 'react'

/** A CSS media query as state: re-renders when the window crosses it. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', notify)
      return () => m.removeEventListener('change', notify)
    },
    () => window.matchMedia(query).matches,
    () => true,
  )
}

/** Room for the scenes list, the stage and the inspector side by side. Below this the side panels open as drawers. */
export const useWide = () => useMedia('(min-width: 1280px)')
/** A phone: one column, bigger touch targets. */
export const useNarrow = () => useMedia('(max-width: 767px)')
