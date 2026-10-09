import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useApi } from '@/api/context'
import { useAssets } from '@/api/hooks'

/** What the script mentions that the library can and cannot draw. Asks the server once the person pauses typing, and again
 *  whenever the library changes (an asset was added), so a missing item turns into a covered one by itself. */
export function useScriptCoverage(script: string, delayMs = 700) {
  const api = useApi()
  const assets = useAssets().data?.assets
  const [settled, setSettled] = useState(script)
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(script), delayMs)
    return () => window.clearTimeout(id)
  }, [script, delayMs])
  const library = assets ? assets.map((a) => `${a.name}:${a.version}`).join(',') : ''
  return useQuery({
    queryKey: ['coverage', settled, library],
    queryFn: () => api.scriptAssets(settled),
    enabled: settled.trim().length > 10,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}
