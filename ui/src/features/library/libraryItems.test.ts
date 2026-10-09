import { describe, expect, it } from 'vitest'
import type { AssetInfo, Catalog, CatalogEntry } from '@/api/types'
import { fromMatches, isAssetSection, itemMatches, libraryItems, matchesQuery } from './libraryItems'

const entry = (name: string, extra: Partial<CatalogEntry> = {}): CatalogEntry => ({ name, summary: `${name} summary`, ...extra })
const asset = (name: string, kind: AssetInfo['kind'], origin: AssetInfo['origin']): AssetInfo => ({
  name,
  kind,
  summary: '',
  tags: [],
  format: 'svg',
  height: 300,
  anchor: [0.5, 1],
  facing: 'right',
  origin,
  credit: '',
  editable: origin === 'user',
  version: 'v1',
  file: `${name}.svg`,
})

const catalog = {
  archetypes: [entry('hero'), entry('cat', { library: 'builtin' }), entry('mine', { library: 'user' }), entry('kid')],
  backgrounds: [entry('forest'), entry('beach', { library: 'builtin' })],
  objects: [entry('car', { library: 'builtin' }), entry('kite', { library: 'user' })],
} as unknown as Catalog

describe('the entries of a section', () => {
  it('know where they come from: the engine, the shipped library, or you', () => {
    const items = libraryItems(catalog, 'characters', [asset('cat', 'character', 'builtin'), asset('mine', 'character', 'user')])
    expect(items.map((i) => [i.entry.name, i.origin])).toEqual([
      ['mine', 'user'], // yours first
      ['hero', 'engine'],
      ['cat', 'builtin'],
      ['kid', 'engine'],
    ])
    expect(items.find((i) => i.entry.name === 'cat')?.asset?.name).toBe('cat')
    expect(items.find((i) => i.entry.name === 'hero')?.asset).toBeUndefined() // the engine's own have no asset behind them
  })

  it('carry the words and colours of the catalog row and of the asset record, once each', () => {
    const rows = { ...catalog, objects: [entry('car', { library: 'builtin', tags: ['car', 'taxi'], roles: ['body'] }), entry('kite', { library: 'user' })] } as unknown as Catalog
    const withWords = { ...asset('car', 'object', 'builtin'), tags: ['taxi', 'गाड़ी'], roles: ['body', 'trim'] }
    const items = libraryItems(rows, 'objects', [withWords, { ...asset('kite', 'object', 'user'), tags: ['पतंग'], roles: ['paper'] }])
    const car = items.find((i) => i.entry.name === 'car')!
    expect(car.tags).toEqual(['car', 'taxi', 'गाड़ी'])
    expect(car.roles).toEqual(['body']) // the catalog row says it first
    const kite = items.find((i) => i.entry.name === 'kite')!
    expect(kite.tags).toEqual(['पतंग']) // the row has none: the asset's stand in
    expect(kite.roles).toEqual(['paper'])
    expect(itemMatches(kite, 'पतंग')).toBe(true)
    expect(itemMatches(car, 'गाड़ी')).toBe(true)
    expect(itemMatches(car, 'zebra')).toBe(false)
  })

  it('wait for the asset list without breaking, and match an asset by kind as well as name', () => {
    expect(libraryItems(catalog, 'objects', undefined).map((i) => i.asset)).toEqual([undefined, undefined])
    // a character called "car" is not the object "car"
    const items = libraryItems(catalog, 'objects', [asset('car', 'character', 'builtin')])
    expect(items.find((i) => i.entry.name === 'car')?.asset).toBeUndefined()
  })

  it('treat a catalog without objects (an older server) as having none', () => {
    const old = { ...catalog, objects: undefined } as unknown as Catalog
    expect(libraryItems(old, 'objects', [])).toEqual([])
  })

  it('know which sections come from the asset library', () => {
    expect(['characters', 'objects', 'backgrounds'].every(isAssetSection)).toBe(true)
    expect(['actions', 'props', 'sounds'].some(isAssetSection)).toBe(false)
  })
})

describe('the From filter', () => {
  it('counts everything Reel ships as built in, the engine’s own too', () => {
    expect(fromMatches('all', 'engine')).toBe(true)
    expect(fromMatches('all', 'user')).toBe(true)
    expect(fromMatches('builtin', 'engine')).toBe(true)
    expect(fromMatches('builtin', 'builtin')).toBe(true)
    expect(fromMatches('builtin', 'user')).toBe(false)
    expect(fromMatches('user', 'user')).toBe(true)
    expect(fromMatches('user', 'builtin')).toBe(false)
    expect(fromMatches('user', 'engine')).toBe(false)
  })
})

describe('the search', () => {
  const car = entry('auto_rickshaw', { summary: 'A green and yellow three-wheeler', tags: ['tuk tuk', 'रिक्शा', 'तीन पहिया', 'गाड़ी'] })

  it('finds a word of the name (with or without the underscore), the summary or the words', () => {
    for (const q of ['rickshaw', 'auto rickshaw', 'auto_rickshaw', 'three-wheeler', 'yellow', 'tuk', 'रिक्शा', 'पहिया']) expect(matchesQuery(car, q), q).toBe(true)
  })

  it('needs every word typed, in any order', () => {
    expect(matchesQuery(car, 'yellow green')).toBe(true)
    expect(matchesQuery(car, 'green purple')).toBe(false)
    expect(matchesQuery(car, 'तीन रिक्शा')).toBe(true)
  })

  it('ignores case and spaces around the words, and an empty search finds all', () => {
    expect(matchesQuery(car, '  AUTO  ')).toBe(true)
    expect(matchesQuery(car, '')).toBe(true)
    expect(matchesQuery(car, '   ')).toBe(true)
  })

  it('compares a Hindi letter with a dot the same whether it was typed whole or as two parts', () => {
    const whole = 'ड़' // ड़ as one character
    const parts = 'ड़' // ड + nukta
    const withParts = entry('x', { tags: [`गा${parts}ी`] })
    expect(matchesQuery(withParts, `गा${whole}ी`)).toBe(true)
    expect(matchesQuery(entry('y', { tags: [`गा${whole}ी`] }), `गा${parts}ी`)).toBe(true)
  })

  it('does not find what is not there', () => {
    expect(matchesQuery(entry('car', { summary: 'A small red car' }), 'zebra')).toBe(false)
    expect(matchesQuery({ name: 'bare' }, 'bare')).toBe(true) // no summary, no words
  })
})
