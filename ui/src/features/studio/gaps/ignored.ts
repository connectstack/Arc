import { create } from 'zustand'

/** A gap's name as the list of the ones a person said they do not need ("object:ball"): the same thing, however the script wrote it. */
export const gapKey = (g: { kind: string; name: string }): string => `${g.kind}:${g.name.trim().toLowerCase()}`

const storeKey = (project: string): string => `reel:gaps-ignored:${project}`

function read(project: string): string[] {
  try {
    const v: unknown = JSON.parse(window.localStorage.getItem(storeKey(project)) ?? '[]')
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

function write(project: string, list: string[]): void {
  try {
    window.localStorage.setItem(storeKey(project), JSON.stringify(list))
  } catch {
    /* a private window keeps nothing: the choice then lasts until the page is closed */
  }
}

interface IgnoredState {
  lists: Record<string, string[]>
  ignore: (project: string, key: string) => void
  restore: (project: string) => void
}

/** What the person said they do not need, per reel. It is theirs alone (this browser), not part of the reel. */
export const useIgnored = create<IgnoredState>((set, get) => ({
  lists: {},
  ignore: (project, key) => {
    const list = [...new Set([...(get().lists[project] ?? read(project)), key])]
    write(project, list)
    set({ lists: { ...get().lists, [project]: list } })
  },
  restore: (project) => {
    write(project, [])
    set({ lists: { ...get().lists, [project]: [] } })
  },
}))

/** The gaps of a reel the person has set aside. */
export function ignoredOf(lists: Record<string, string[]>, project: string | null): Set<string> {
  return new Set(project ? (lists[project] ?? read(project)) : [])
}
