import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/types'
import type { AssetDraft, AssetInfo } from '@/api/types'
import { KIND_HEIGHT, addWords, changesFromForm, describeError, draftLook, extraWords, fieldsFromForm, formFromAsset, formFromDraft, nameProblem, slugName, sizeText, specSnippet, splitWords, trimName } from './assetFields'

const draft = (over: Partial<AssetDraft['suggested']> = {}): AssetDraft => ({
  id: 'd1',
  filename: 'Red Kite.svg',
  format: 'svg',
  bytes: 100,
  suggested: { name: 'red_kite', kind: 'object', summary: '', tags: [], height: 300, anchor: [0.5, 1], facing: 'right', ...over },
  notes: [],
  roles: [],
  aspect: 1,
})

const info = (over: Partial<AssetInfo> = {}): AssetInfo => ({
  name: 'dragon',
  kind: 'character',
  summary: 'A purple dragon',
  tags: ['dragon', 'wyvern', 'ड्रैगन'],
  format: 'svg',
  height: 450,
  anchor: [0.5, 0.94],
  facing: 'right',
  origin: 'user',
  credit: '',
  roles: ['scales', 'belly'],
  editable: true,
  version: 'v1',
  file: 'dragon.svg',
  ...over,
})

describe('names', () => {
  it('become lower-case ids with underscores, starting with a letter', () => {
    expect(slugName('Fire Dragon')).toBe('fire_dragon')
    expect(slugName('  café   au lait ')).toBe('cafe_au_lait_')
    expect(slugName('3 dogs')).toBe('a_3_dogs')
    expect(slugName('x'.repeat(60))).toHaveLength(40)
    expect(trimName(slugName('fire dragon '))).toBe('fire_dragon')
  })

  it('say what is wrong with one that cannot be used', () => {
    expect(nameProblem('')).toMatch(/give it a name/i)
    expect(nameProblem('Fire')).toMatch(/lower-case/i)
    expect(nameProblem('fire_dragon')).toBeNull()
  })
})

describe('words', () => {
  it('are kept one way: trimmed, in lower case, once each, up to the most an asset keeps', () => {
    expect(addWords(['a'], [' B ', 'a', 'Fire   Dragon', ''])).toEqual(['a', 'b', 'fire dragon'])
    const same = ['a']
    expect(addWords(same, ['A'])).toBe(same) // nothing new: the very same list
    expect(addWords([], Array.from({ length: 60 }, (_, i) => `w${i}`))).toHaveLength(40)
  })

  it('are split at commas, semicolons, line breaks and the commas of other scripts', () => {
    expect(splitWords('cat, kitten; बिल्ली\nkitty،dog、bird，fish')).toEqual(['cat', 'kitten', 'बिल्ली', 'kitty', 'dog', 'bird', 'fish'])
  })

  it('leave out the asset’s own name when a card shows them', () => {
    expect(extraWords({ name: 'fire_dragon', tags: ['fire dragon', 'fire_dragon', 'drake'] })).toEqual(['drake'])
  })
})

describe('the form for a file just read', () => {
  it('starts from what the server suggests, with what the caller knew laid over it', () => {
    const f = formFromDraft(draft(), { name: 'Fire Dragon', kind: 'character', summary: 'A dragon', tags: ['Dragon', 'ड्रैगन'] })
    expect(f).toMatchObject({ name: 'fire_dragon', kind: 'character', summary: 'A dragon', tags: ['dragon', 'ड्रैगन'], height: KIND_HEIGHT.character, cutout: null })
    expect(formFromDraft(draft()).name).toBe('red_kite')
  })
})

describe('what the API is given', () => {
  it('has a size and anchor for a thing, and none for a place (it is the whole frame)', () => {
    const thing = fieldsFromForm(formFromDraft(draft()), false)
    expect(thing).toMatchObject({ name: 'red_kite', kind: 'object', height: 300, anchor: [0.5, 1], facing: 'right' })
    expect(thing).not.toHaveProperty('place')
    expect(thing).not.toHaveProperty('cutout') // an SVG has no background to remove

    const place = fieldsFromForm({ ...formFromDraft(draft()), kind: 'place' }, true)
    expect(place).toMatchObject({ kind: 'place', height: KIND_HEIGHT.place, anchor: [0.5, 1] })
    expect(place).toHaveProperty('place')
    expect(place).not.toHaveProperty('cutout') // nothing is cut out of a backdrop
  })

  it('has the background choice for a picture of a thing', () => {
    expect(fieldsFromForm(formFromDraft(draft()), true)).toHaveProperty('cutout', null)
    expect(fieldsFromForm({ ...formFromDraft(draft()), cutout: false }, true)).toHaveProperty('cutout', false)
  })

  it('sends only what an edit changed', () => {
    const before = formFromAsset(info())
    expect(changesFromForm(before, before, false)).toEqual({})
    expect(changesFromForm(before, { ...before, summary: 'Purple', tags: [...before.tags, 'drake'] }, false)).toEqual({ summary: 'Purple', tags: ['dragon', 'wyvern', 'ड्रैगन', 'drake'] })
    expect(changesFromForm(before, { ...before, anchor: [0.5, 0.5] }, false)).toEqual({ anchor: [0.5, 0.5] })
  })
})

describe('how an unsaved file is drawn', () => {
  it('follows the settings of a thing, and the time of day of a place', () => {
    const f = formFromDraft(draft())
    expect(draftLook(f, false, true, 'dusk')).toEqual({ kind: 'object', trueScale: true, timeOfDay: 'dusk', height: 300, anchor: [0.5, 1], facing: 'right' })
    expect(draftLook(f, true, false, 'day')).toHaveProperty('cutout', null)
    expect(draftLook({ ...f, kind: 'place' }, true, true, 'night')).toEqual({ kind: 'place', trueScale: false, timeOfDay: 'night' })
  })
})

describe('words for a person', () => {
  it('sizes against a person, a place being the whole frame', () => {
    expect(sizeText({ kind: 'object', height: 287.5 })).toBe('0.5 × a person')
    expect(sizeText({ kind: 'place', height: 1920 })).toBe('the whole frame')
  })

  it('gives the server’s own message and hint for an error', () => {
    expect(describeError(new ApiError(415, 'not a picture', 'use an SVG'))).toEqual({ detail: 'not a picture', hint: 'use an SVG' })
    expect(describeError(new Error('boom'))).toEqual({ detail: 'boom' })
    expect(describeError('odd')).toEqual({ detail: 'odd' })
  })

  it('writes the spec line that uses an asset, naming the first colour it can change', () => {
    expect(specSnippet(info({ kind: 'object', name: 'car', roles: ['body'] }))).toEqual({ where: 'scenes[].objects[]', json: '{"asset": "car", "position": [0.5, 0.8], "palette": {"body": "#2a6fdb"}}' })
    expect(specSnippet(info({ kind: 'character', roles: [] })).json).toBe('{"id": "pal", "archetype": "dragon"}')
    expect(specSnippet(info({ kind: 'place', name: 'beach' }))).toEqual({ where: 'scenes[].background.template', json: '{"template": "beach"}' })
  })
})
