// What the Library lists for characters, objects and places: the engine's own entries and the asset library's, as one list that can be
// filtered by where it comes from and searched by name, summary and words. No React in here.
import type { AssetInfo, AssetKind, Catalog, CatalogEntry } from '@/api/types'

/** The Library sections whose entries can come from the asset library. */
export type AssetSection = 'characters' | 'objects' | 'backgrounds'

/** Where an entry comes from: the engine itself, the library that ships with Reel, or the person's own files. */
export type Origin = 'engine' | 'builtin' | 'user'

/** The "From" filter: everything, what Reel ships with (the engine's own and the built-in library), or the person's own. */
export type From = 'all' | 'builtin' | 'user'

export const SECTION_KIND: Record<AssetSection, AssetKind> = { characters: 'character', objects: 'object', backgrounds: 'place' }

export const SECTION_OF_KIND: Record<AssetKind, AssetSection> = { character: 'characters', object: 'objects', place: 'backgrounds' }

export const isAssetSection = (section: string): section is AssetSection => section === 'characters' || section === 'objects' || section === 'backgrounds'

export interface LibraryItem {
  entry: CatalogEntry
  origin: Origin
  /** the library's record of it (library entries only; absent while the asset list is loading) */
  asset?: AssetInfo
  /** the words that find it: the catalog row's and the asset's, once each */
  tags: string[]
  /** the colours a spec can change */
  roles: string[]
}

export function sectionEntries(catalog: Catalog, section: AssetSection): CatalogEntry[] {
  return section === 'characters' ? catalog.archetypes : section === 'objects' ? (catalog.objects ?? []) : catalog.backgrounds
}

/** The entries of a section with their origin and asset record. The person's own come first (they are few, and the thing just added should be seen). */
export function libraryItems(catalog: Catalog, section: AssetSection, assets: AssetInfo[] | undefined): LibraryItem[] {
  const kind = SECTION_KIND[section]
  const byName = new Map((assets ?? []).filter((a) => a.kind === kind).map((a) => [a.name, a]))
  const items = sectionEntries(catalog, section).map((entry): LibraryItem => {
    const asset = entry.library ? byName.get(entry.name) : undefined
    return { entry, origin: entry.library ?? 'engine', asset, tags: [...new Set([...(entry.tags ?? []), ...(asset?.tags ?? [])])], roles: entry.roles ?? asset?.roles ?? [] }
  })
  return [...items.filter((i) => i.origin === 'user'), ...items.filter((i) => i.origin !== 'user')]
}

export const fromMatches = (from: From, origin: Origin): boolean => from === 'all' || (from === 'user' ? origin === 'user' : origin !== 'user')

/** Text as it is compared: composed (a Hindi letter with its dot is one form), lower case. */
const fold = (text: string): string => text.normalize('NFC').toLowerCase()

/** Does the entry answer the search? Every word typed must be found in its name, its summary or one of its words (any language). */
export const itemMatches = (i: LibraryItem, query: string): boolean => matchesQuery({ name: i.entry.name, summary: i.entry.summary, tags: i.tags }, query)

export function matchesQuery(e: { name: string; summary?: string; tags?: string[] }, query: string): boolean {
  const wanted = fold(query).split(/\s+/).filter(Boolean)
  if (wanted.length === 0) return true
  const haystack = fold([e.name, e.name.replace(/_/g, ' '), e.summary ?? '', ...(e.tags ?? [])].join('\n'))
  return wanted.every((w) => haystack.includes(w))
}
