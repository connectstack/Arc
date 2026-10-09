import { describe, expect, it } from 'vitest'
import { contrast, followedShade, shadeLike } from './colors'

describe('shades that follow a recoloured part', () => {
  // the expected colours are what the engine's own `_shade_like` (reel/assets/art.py) gives for the same inputs
  it('steps the new colour the way the drawing steps from the part to its shade', () => {
    expect(shadeLike('#d57a2c', '#a85a1a', '#2a6fdb')).toBe('#1652af') // a darker shade
    expect(shadeLike('#d57a2c', '#f0a060', '#2a6fdb')).toBe('#5e99f5') // a lighter one
    expect(shadeLike('#808080', '#404040', '#ff0000')).toBe('#800000')
  })

  it('says nothing about colours that are not hex', () => {
    expect(shadeLike('red', '#404040', '#ff0000')).toBeNull()
    expect(shadeLike('#808080', '#404040', 'var(--accent)')).toBeNull()
  })

  it('gives a shade the colour of its recoloured part, unless the shade is named too', () => {
    const drawing = { body: '#d57a2c', body_dark: '#a85a1a', eye: '#23262e' }
    expect(followedShade(drawing, { body: '#2a6fdb' }, 'body_dark')).toBe('#1652af')
    expect(followedShade(drawing, { body: '#2a6fdb', body_dark: '#000000' }, 'body_dark')).toBeNull() // named: its own colour
    expect(followedShade(drawing, {}, 'body_dark')).toBeNull() // nothing recoloured
    expect(followedShade(drawing, { body: '#2a6fdb' }, 'eye')).toBeNull() // not a shade
    expect(followedShade(drawing, { body: '#2a6fdb' }, 'body')).toBeNull()
    expect(followedShade({ leaves: '#3a8a3a' }, { leaves: '#ff0000' }, 'leaves_light')).toBeNull() // the drawing has no such shade
  })
})

describe('contrast', () => {
  it('is 21 between black and white', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 1)
  })
})
