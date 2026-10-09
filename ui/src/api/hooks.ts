import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useApi } from './context'

/** Server data as queries. The catalog never changes while the server runs; the rest is cheap to refresh. */
export function useCatalog() {
  const api = useApi()
  return useQuery({ queryKey: ['catalog'], queryFn: () => api.catalog(), staleTime: Infinity })
}

export function useProjects() {
  const api = useApi()
  return useQuery({ queryKey: ['projects'], queryFn: () => api.listProjects() })
}

export function useExamples() {
  const api = useApi()
  return useQuery({ queryKey: ['examples'], queryFn: () => api.examples(), staleTime: Infinity })
}

export function useHealth() {
  const api = useApi()
  return useQuery({ queryKey: ['health'], queryFn: () => api.health(), retry: false, refetchInterval: (q) => (q.state.status === 'error' ? 4_000 : 30_000) })
}

export function useDoctor(enabled = true) {
  const api = useApi()
  return useQuery({ queryKey: ['doctor'], queryFn: () => api.doctor(), enabled, staleTime: 10_000 })
}

export function useEngines() {
  const api = useApi()
  return useQuery({ queryKey: ['engines'], queryFn: () => api.ttsEngines(), staleTime: 10_000 })
}

export function useRenders() {
  const api = useApi()
  return useQuery({ queryKey: ['renders'], queryFn: () => api.listRenders() })
}

export function useCacheStats() {
  const api = useApi()
  return useQuery({ queryKey: ['cache'], queryFn: () => api.cache() })
}

/** The asset library (characters, objects and places, built in and the workspace's own). */
export function useAssets() {
  const api = useApi()
  return useQuery({ queryKey: ['assets'], queryFn: () => api.listAssets(), staleTime: 30_000 })
}

/** After the library changed (an asset added, edited or removed) everything that lists it must read it again: the
 *  catalog the editor offers and the planners use, the asset list, and thumbnails (their URLs carry the art's version). */
export function useRefreshLibrary() {
  const qc = useQueryClient()
  return () => Promise.all([qc.invalidateQueries({ queryKey: ['assets'] }), qc.invalidateQueries({ queryKey: ['catalog'] })])
}
