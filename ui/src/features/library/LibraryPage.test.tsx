import axe from 'axe-core'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Api } from '@/api/client'
import { addUserAsset, libraryApi, svgFile } from '@/features/assets/testing'
import { renderScreen } from '@/test/harness'
import { LibraryPage } from './LibraryPage'

const openPage = (api: Api) => renderScreen(<LibraryPage />, { api, route: '/library', path: '/library' })
const tab = (name: string) => screen.findByRole('tab', { name })
/** The names of the cards on show, in order. */
const shown = (): string[] => [...document.querySelectorAll('[data-asset]')].map((el) => el.getAttribute('data-asset') ?? '')
/** The button of a library card, found by the name on it. */
const card = async (name: string): Promise<HTMLElement> => (await screen.findByText(name, { selector: 'span' })).closest('button') as HTMLElement
const fact = (dialog: HTMLElement, term: string): string => within(dialog).getByText(term, { selector: 'dt' }).nextElementSibling?.textContent ?? ''
const search = () => screen.getByRole('textbox', { name: /search the library/i })

describe('the library page', () => {
  it('counts what a script can use, from the engine and from the library, and says how much is yours', async () => {
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { tags: ['kite'] })
    await openPage(api)
    // 6 engine characters + cat, cow, teacher; 6 library objects + kite; 7 engine sets + beach, park
    expect(await screen.findByText(/9 characters · 7 objects · 9 places/)).toHaveTextContent(/1 yours/)
    expect(screen.getByRole('button', { name: /add asset/i })).toBeInTheDocument()
  })

  it('has an Objects tab: each library object with its badge, size, colours and first words (all of them in a tooltip)', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Objects'))
    const car = await card('car')
    expect(within(car).getByText('Library')).toBeInTheDocument()
    expect(within(car).getByText('A small red car, side view')).toBeInTheDocument()
    expect(within(car).getByText('0.52 × a person')).toBeInTheDocument()
    expect(within(car).getByText('Colours: body, body_dark')).toBeInTheDocument()
    const words = within(car).getByText(/cars · taxi · automobile/)
    expect(words).toHaveTextContent('+5')
    expect(words.title).toContain('गाड़ी')
    expect(words.title).toContain('sedan')
    expect(shown()).toEqual(expect.arrayContaining(['car', 'cake', 'chair', 'balloon', 'tree', 'book']))
  })

  it('draws the library’s pictures in the style chosen on the page', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const thumb = vi.spyOn(api, 'assetThumbUrl')
    await openPage(api)
    await user.click(await tab('Objects'))
    await card('car')
    expect(thumb.mock.calls.length).toBeGreaterThan(0)
    thumb.mockClear()
    await user.click(screen.getByRole('radio', { name: 'Stickman' }))
    await waitFor(() => expect(thumb).toHaveBeenCalled())
    expect(thumb.mock.calls.every((c) => c[1] === 'stickman')).toBe(true)
    // one picture per card, asked for with the asset's own version
    expect(thumb.mock.calls.some((c) => c[0].name === 'car' && c[0].version === 'seed')).toBe(true)
  })

  it('shows the engine’s own characters and the library’s side by side; only the library’s are marked', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Characters'))
    await screen.findByText('hero')
    expect(shown()).toEqual(expect.arrayContaining(['hero', 'kid', 'cat', 'cow', 'teacher']))
    const engine = document.querySelector('[data-asset="hero"]') as HTMLElement
    expect(within(engine).queryByText('Library')).not.toBeInTheDocument()
    expect(within(document.querySelector('[data-asset="cat"]') as HTMLElement).getByText('Library')).toBeInTheDocument()
  })

  it('shows the engine’s sets and the library’s places under Backgrounds', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Backgrounds'))
    await screen.findByText('forest')
    expect(shown()).toEqual(expect.arrayContaining(['forest', 'street', 'beach', 'park']))
    const beach = document.querySelector('[data-asset="beach"]') as HTMLElement
    expect(within(beach).getByText('Library')).toBeInTheDocument()
    expect(within(beach).getByText('3 standing spots')).toBeInTheDocument()
  })
})

describe('the From filter', () => {
  it('puts your own first, and narrows to what is yours or what ships with Reel', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite', tags: ['kite', 'पतंग'] })
    await openPage(api)
    await user.click(await tab('Objects'))
    await card('kite')
    expect(shown()[0]).toBe('kite')
    expect(within(document.querySelector('[data-asset="kite"]') as HTMLElement).getByText('Yours')).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Yours' }))
    expect(shown()).toEqual(['kite'])
    await user.click(screen.getByRole('radio', { name: 'Built in' }))
    expect(shown()).not.toContain('kite')
    expect(shown()).toContain('car')
    await user.click(screen.getByRole('radio', { name: 'All' }))
    expect(shown()).toEqual(expect.arrayContaining(['kite', 'car']))
  })

  it('counts the engine’s own characters as built in', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Characters'))
    await screen.findByText('hero')
    await user.click(screen.getByRole('radio', { name: 'Built in' }))
    expect(shown()).toEqual(expect.arrayContaining(['hero', 'cat']))
  })

  it('explains how to add one when "Yours" is empty, and the button there adds the kind being looked at', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await openPage(api)
    await user.click(await tab('Characters'))
    await screen.findByText('hero')
    await user.click(screen.getByRole('radio', { name: 'Yours' }))
    expect(await screen.findByText(/you have not added any characters yet/i)).toBeInTheDocument()
    expect(screen.getByText(/drop a picture on this page/i)).toBeInTheDocument()
    expect(screen.getByText(/plain background gets it removed automatically/i)).toBeInTheDocument()
    expect(screen.getByText('(in memory)/assets')).toBeInTheDocument() // where they are saved
    expect(shown()).toEqual([])

    const [, inPage] = screen.getAllByRole('button', { name: /add asset/i })
    await user.click(inPage)
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    fireEvent.drop(within(dialog).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('my_hero.svg')], types: ['Files'] } })
    await within(dialog).findByRole('textbox', { name: 'Name' })
    expect(within(dialog).getByRole('radio', { name: 'Character' })).toBeChecked()
  })
})

describe('searching', () => {
  it('matches the name, the summary and the words, in Hindi too', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite', tags: ['kite', 'पतंग', 'flying toy'] })
    await openPage(api)
    await user.click(await tab('Objects'))
    await card('car')

    await user.type(search(), 'गाड़ी')
    expect(shown()).toEqual(['car'])
    await user.clear(search())
    await user.type(search(), 'पतंग')
    expect(shown()).toEqual(['kite'])
    await user.clear(search())
    await user.type(search(), 'taxi') // a word of the car
    expect(shown()).toEqual(['car'])
    await user.clear(search())
    await user.type(search(), 'paper red') // words of a summary, in any order
    expect(shown()).toEqual(['kite'])
    await user.clear(search())
    await user.type(search(), 'candles') // the summary of the cake
    expect(shown()).toEqual(['cake'])
    await user.clear(search())
    await user.type(search(), 'zebra')
    expect(await screen.findByText('Nothing matches.')).toBeInTheDocument()
  })

  it('finds a character by its Hindi word too', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Characters'))
    await screen.findByText('hero')
    await user.type(search(), 'बिल्ली')
    expect(shown()).toEqual(['cat'])
  })
})

describe('a library asset, closely', () => {
  it('shows the three styles with the true-size switch, its facts, colours and words, and is read-only when it is built in', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const thumb = vi.spyOn(api, 'assetThumbUrl')
    await openPage(api)
    await user.click(await tab('Objects'))
    await user.click(await card('car'))

    const dialog = await screen.findByRole('dialog', { name: 'Car' })
    expect(within(dialog).getByText('A small red car, side view')).toBeInTheDocument()
    expect(within(dialog).getAllByRole('img', { name: /drawn in the .+ style/i })).toHaveLength(3)
    expect(fact(dialog, 'Kind')).toBe('Object')
    expect(fact(dialog, 'Size')).toBe('0.52 × a person (300 px tall)')
    expect(fact(dialog, 'Anchor')).toBe('Custom (0.5, 0.9)')
    expect(fact(dialog, 'Facing')).toBe('Right')
    expect(fact(dialog, 'Format')).toBe('SVG drawing')
    expect(fact(dialog, 'File')).toBe('car.svg')
    expect(fact(dialog, 'Credit')).toBe('drawn for reel, CC0')

    // the colours a spec can change, with the drawing's own colour
    const colours = within(within(dialog).getByRole('region', { name: 'Colours a spec can change' })).getAllByRole('listitem')
    expect(colours.map((li) => li.textContent)).toEqual(['body#d64541', 'body_dark#a93226'])
    // the words that find it, in every language
    const words = within(within(dialog).getByRole('region', { name: 'Words that find it' }))
    expect(words.getByText('गाड़ी')).toBeInTheDocument()
    expect(words.getByText('taxi')).toBeInTheDocument()
    // and how a spec uses it
    expect(within(dialog).getByText(/"asset": "car"/)).toBeInTheDocument()

    // true size: the previews are asked for with a person beside it
    await user.click(within(dialog).getByRole('switch', { name: /next to a person/i }))
    await waitFor(() => expect(thumb).toHaveBeenLastCalledWith(expect.objectContaining({ name: 'car' }), 'stickman', undefined, true))

    // built in: nothing to change, and how to put your own in its place
    expect(within(dialog).getByText('Built-in assets are read-only')).toBeInTheDocument()
    expect(within(dialog).getByText(/add your own with the same name, “car”/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /^edit/i })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('link', { name: /download/i })).not.toBeInTheDocument()
  })

  it('offers "Make my own version", which opens Add asset with the same name and words', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await tab('Objects'))
    await user.click(await card('cake'))
    const detail = await screen.findByRole('dialog', { name: 'Cake' })
    await user.click(within(detail).getByRole('button', { name: /make my own version/i }))

    const add = await screen.findByRole('dialog', { name: 'Add asset' })
    expect(screen.queryByRole('dialog', { name: 'Cake' })).not.toBeInTheDocument()
    expect(within(add).getByText(/instead of the built-in “cake”/)).toBeInTheDocument()
    fireEvent.drop(within(add).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('my_cake.svg')], types: ['Files'] } })
    expect(await within(add).findByRole('textbox', { name: 'Name' })).toHaveValue('cake')
    expect(within(add).getByRole('button', { name: 'Remove केक' })).toBeInTheDocument()
    await user.click(within(add).getByRole('button', { name: /save to library/i }))
    expect(await within(add).findByText(/there is already a built-in asset named “cake”/i)).toBeInTheDocument()
    await user.click(within(add).getByRole('button', { name: /replace it with mine/i }))
    // now it is yours
    await waitFor(() => expect(document.querySelector('[data-asset="cake"]')).toHaveTextContent('Yours'))
  })

  it('lets you edit, download and delete your own', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite', tags: ['kite', 'पतंग'], credit: 'drawn by me' })
    await openPage(api)
    await user.click(await tab('Objects'))
    await user.click(await card('kite'))

    // download: the art, as a file
    let dialog = await screen.findByRole('dialog', { name: 'Kite' })
    const download = within(dialog).getByRole('link', { name: /download art/i })
    expect(download).toHaveAttribute('href', api.assetArtUrl('kite'))
    expect(download).toHaveAttribute('download', 'kite.svg')
    expect(fact(dialog, 'Credit')).toBe('drawn by me')
    expect(within(dialog).queryByText('Built-in assets are read-only')).not.toBeInTheDocument()

    // edit: the same settings as when it was added, starting on the name
    await user.click(within(dialog).getByRole('button', { name: /^edit/i }))
    expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveFocus()
    const summary = within(dialog).getByRole('textbox', { name: 'Summary' })
    expect(summary).toHaveValue('A red paper kite')
    await user.clear(summary)
    await user.type(summary, 'A blue paper kite')
    await user.type(within(dialog).getByLabelText('Words'), 'diamond{Enter}')
    await user.click(within(dialog).getByRole('button', { name: /save changes/i }))
    expect(await screen.findByText('Saved “kite”')).toBeInTheDocument()
    await waitFor(() => expect(within(dialog).getByText('A blue paper kite')).toBeInTheDocument())
    const saved = (await api.listAssets()).assets.find((a) => a.name === 'kite')
    expect(saved).toMatchObject({ summary: 'A blue paper kite' })
    expect(saved?.tags).toContain('diamond')
    expect(within(dialog).getByRole('button', { name: /^edit/i })).toHaveFocus() // back to the closer look, on the button that led out of it

    // delete: asks first
    await user.click(within(dialog).getByRole('button', { name: /delete/i }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete “kite”?' })
    await user.click(within(confirm).getByRole('button', { name: /^cancel$/i }))
    expect((await api.listAssets()).assets.some((a) => a.name === 'kite')).toBe(true)
    dialog = await screen.findByRole('dialog', { name: 'Kite' })
    await user.click(within(dialog).getByRole('button', { name: /delete/i }))
    await user.click(within(await screen.findByRole('dialog', { name: 'Delete “kite”?' })).getByRole('button', { name: /delete asset/i }))

    expect(await screen.findByText('Deleted “kite”')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(shown()).not.toContain('kite'))
    expect((await api.listAssets()).assets.some((a) => a.name === 'kite')).toBe(false)
    expect((await api.catalog()).objects.some((o) => o.name === 'kite')).toBe(false)
  })

  it('shows a place with its own facts, and a time of day instead of the true-size switch', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const thumb = vi.spyOn(api, 'assetThumbUrl')
    await openPage(api)
    await user.click(await tab('Backgrounds'))
    await user.click(await card('beach'))
    const dialog = await screen.findByRole('dialog', { name: 'Beach' })
    expect(fact(dialog, 'Kind')).toBe('Place')
    expect(fact(dialog, 'Size')).toBe('The whole frame')
    expect(fact(dialog, 'Ground line')).toBe('80% down the frame')
    expect(fact(dialog, 'Horizon')).toBe('56% down the frame')
    expect(fact(dialog, 'Standing spots')).toBe('left, center, right')
    expect(within(dialog).queryByText('Anchor', { selector: 'dt' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('switch', { name: /next to a person/i })).not.toBeInTheDocument()
    expect(within(dialog).getByText(/"template": "beach"/)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('combobox', { name: 'Time of day' }))
    await user.click(await screen.findByRole('option', { name: 'Night' }))
    await waitFor(() => expect(thumb).toHaveBeenLastCalledWith(expect.objectContaining({ name: 'beach' }), 'stickman', 'night', false))
  })

  it('says why a name cannot be used when you rename one to a name that is taken', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite' })
    await openPage(api)
    await user.click(await tab('Objects'))
    await user.click(await card('kite'))
    const dialog = await screen.findByRole('dialog', { name: 'Kite' })
    await user.click(within(dialog).getByRole('button', { name: /^edit/i }))
    const name = within(dialog).getByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'car')
    expect(within(dialog).getByText(/will not find it under the new name/i)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: /save changes/i }))
    expect(await within(dialog).findByText(/there is already an asset named 'car'/i)).toBeInTheDocument()
    expect(name).toHaveAttribute('aria-invalid', 'true')
    // nothing was lost
    expect((await api.listAssets()).assets.some((a) => a.name === 'kite')).toBe(true)
  })
})

describe('adding from the page', () => {
  it('has an Add asset button that starts on the kind of the tab being looked at', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const onAdd = vi.spyOn(api, 'createAssetDraft')
    await openPage(api)
    await user.click(await tab('Characters'))
    await user.click(screen.getByRole('button', { name: /add asset/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    fireEvent.drop(within(dialog).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('mouse.svg')], types: ['Files'] } })
    await within(dialog).findByRole('textbox', { name: 'Name' })
    expect(onAdd).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({ kind: 'character' }))
    expect(within(dialog).getByRole('radio', { name: 'Character' })).toBeChecked()

    await user.click(within(dialog).getByRole('button', { name: /save to library/i }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add asset' })).not.toBeInTheDocument())
    // listed at once, among the characters, marked as yours
    await waitFor(() => expect(shown()[0]).toBe('mouse'))
    expect(within(document.querySelector('[data-asset="mouse"]') as HTMLElement).getByText('Yours')).toBeInTheDocument()
  })

  it('shows what was just added where it belongs, whichever tab it was added from', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await openPage(api)
    await screen.findByText('walk') // the Actions tab
    await user.type(search(), 'wave')
    await user.click(screen.getByRole('button', { name: /add asset/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    fireEvent.drop(within(dialog).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('lamp.svg')], types: ['Files'] } })
    await within(dialog).findByRole('textbox', { name: 'Name' })
    await user.click(within(dialog).getByRole('button', { name: /save to library/i }))

    // the page moved to the Objects tab, cleared the search, and lists the new object first
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Objects' })).toHaveAttribute('aria-selected', 'true'))
    await waitFor(() => expect(shown()[0]).toBe('lamp'))
    expect(search()).toHaveValue('')
    expect(shown()).toContain('car')
  })

  it('accepts a file dropped anywhere on the page, with a clear overlay while it is dragged in', async () => {
    const api = await libraryApi()
    await openPage(api)
    const heading = await screen.findByRole('heading', { name: 'Library' })
    expect(screen.queryByText(/drop a picture to add it to the library/i)).not.toBeInTheDocument()

    fireEvent.dragOver(heading, { dataTransfer: { types: ['Files'] } })
    expect(await screen.findByText(/drop a picture to add it to the library/i)).toBeInTheDocument()
    fireEvent.dragLeave(heading, { relatedTarget: null })
    await waitFor(() => expect(screen.queryByText(/drop a picture to add it to the library/i)).not.toBeInTheDocument())

    fireEvent.drop(heading, { dataTransfer: { files: [svgFile('dragon_pic.svg')], types: ['Files'] } })
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    // straight to the form: the file came with the drop
    expect(await within(dialog).findByRole('textbox', { name: 'Name' })).toHaveValue('dragon_pic')
    expect(within(dialog).queryByRole('button', { name: /drop an svg/i })).not.toBeInTheDocument()
  })

  it('leaves the page alone while the Add asset dialog takes the drop, and shows no overlay after it closes', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    await user.click(await screen.findByRole('button', { name: /add asset/i }))
    const dialog = await screen.findByRole('dialog', { name: 'Add asset' })
    const zone = within(dialog).getByRole('button', { name: /drop an svg/i })
    fireEvent.dragOver(zone, { dataTransfer: { types: ['Files'] } })
    expect(screen.queryByText(/drop a picture to add it to the library/i)).not.toBeInTheDocument()
    fireEvent.drop(zone, { dataTransfer: { files: [svgFile('lamp.svg')], types: ['Files'] } })
    await within(dialog).findByRole('textbox', { name: 'Name' }) // taken by the dialog, not the page: one dialog, one file
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    await user.click(within(dialog).getByRole('button', { name: /^cancel$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByText(/drop a picture to add it to the library/i)).not.toBeInTheDocument()
  })

  it('ignores something dragged that is not a file', async () => {
    await openPage(await libraryApi())
    const heading = await screen.findByRole('heading', { name: 'Library' })
    fireEvent.dragOver(heading, { dataTransfer: { types: ['text/plain'] } })
    expect(screen.queryByText(/drop a picture to add it to the library/i)).not.toBeInTheDocument()
  })

  it('lists the files of the library it could not read, and where the assets live', async () => {
    const api = await libraryApi()
    const real = await api.listAssets()
    vi.spyOn(api, 'listAssets').mockResolvedValue({
      ...real,
      folder: '/Users/me/studio/assets',
      problems: [
        { file: 'broken.svg', message: 'this SVG has no size' },
        { file: 'huge.png', message: 'this file is larger than 12 MB' },
      ],
    })
    await openPage(api)
    expect(await screen.findByText(/2 files in your assets folder could not be read/i)).toBeInTheDocument()
    expect(screen.getByText('broken.svg')).toBeInTheDocument()
    expect(screen.getByText(/this SVG has no size/)).toBeInTheDocument()
    expect(screen.getByText('huge.png')).toBeInTheDocument()
    expect(screen.getByText('/Users/me/studio/assets')).toBeInTheDocument()
  })

  it('says nothing about problems when there are none', async () => {
    await openPage(await libraryApi())
    await screen.findByText(/9 characters/)
    expect(screen.queryByText(/could not be read/i)).not.toBeInTheDocument()
  })
})

describe('the other sections', () => {
  it('still list the actions, with their search, as before', async () => {
    const user = userEvent.setup()
    await openPage(await libraryApi())
    expect(await screen.findByText('walk')).toBeInTheDocument()
    await user.type(search(), 'tears')
    expect(await screen.findByText('cry')).toBeInTheDocument()
    expect(screen.queryByText('walk')).not.toBeInTheDocument()
  })
})

describe('accessibility', () => {
  /** What axe can judge without layout (names, roles, labels, relationships); colour contrast is covered by the token tests. */
  async function violations(root: Element): Promise<string[]> {
    const result = await axe.run(root, { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } })
    return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)
  }

  it('has no violations on the Objects tab, in the closer look (built in, yours, being edited), the delete question and Add asset', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite', { summary: 'A red paper kite', tags: ['kite', 'पतंग'], credit: 'drawn by me' })
    await openPage(api)
    await user.click(await tab('Objects'))
    await card('cake')
    expect(await violations(document.body)).toEqual([])

    await user.click(await card('cake'))
    expect(await violations(await screen.findByRole('dialog', { name: 'Cake' }))).toEqual([])
    await user.click(within(screen.getByRole('dialog', { name: 'Cake' })).getByRole('button', { name: 'Close' }))

    await user.click(await card('kite'))
    const detail = await screen.findByRole('dialog', { name: 'Kite' })
    expect(await violations(detail)).toEqual([])
    await user.click(within(detail).getByRole('button', { name: /^edit/i }))
    expect(await violations(detail)).toEqual([])
    await user.click(within(detail).getByRole('button', { name: /^cancel$/i }))
    await user.click(within(detail).getByRole('button', { name: /delete/i }))
    expect(await violations(await screen.findByRole('dialog', { name: 'Delete “kite”?' }))).toEqual([])
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    await user.click(within(detail).getByRole('button', { name: 'Close' }))

    await user.click(screen.getByRole('button', { name: /add asset/i }))
    const add = await screen.findByRole('dialog', { name: 'Add asset' })
    expect(await violations(add)).toEqual([])
    fireEvent.drop(within(add).getByRole('button', { name: /drop an svg/i }), { dataTransfer: { files: [svgFile('lamp.svg')], types: ['Files'] } })
    await within(add).findByRole('textbox', { name: 'Name' })
    expect(await violations(add)).toEqual([])
  })
})
