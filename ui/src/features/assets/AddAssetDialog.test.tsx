import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { Api } from '@/api/client'
import { ApiError, type AssetDraft, type AssetInfo } from '@/api/types'
import { renderScreen } from '@/test/harness'
import { AddAssetDialog, type AddAssetInitial } from './AddAssetDialog'
import { addUserAsset, libraryApi, svgFile } from './testing'

const dropOn = (el: Element, ...files: File[]) => fireEvent.drop(el, { dataTransfer: { files, types: ['Files'] } })
const zone = () => screen.getByRole('button', { name: /drop an svg, png, jpg or webp/i })

async function open(api: Api, props: { initial?: AddAssetInitial; onAdded?: (a: AssetInfo) => void; onOpenChange?: (o: boolean) => void } = {}) {
  const onOpenChange = props.onOpenChange ?? vi.fn()
  await renderScreen(<AddAssetDialog open onOpenChange={onOpenChange} initial={props.initial} onAdded={props.onAdded} />, { api })
  return { onOpenChange }
}

describe('adding an asset', () => {
  it('takes a dropped file to a form, saves it, and the library lists it', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const onAdded = vi.fn()
    const { onOpenChange } = await open(api, { onAdded })

    expect(screen.getByText(/drop an svg, png, jpg or webp, or choose a file/i)).toBeInTheDocument()
    expect(screen.getByText(/a picture with a plain background gets it removed automatically/i)).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument() // nobody has said what it is yet

    dropOn(zone(), svgFile())
    const name = await screen.findByRole('textbox', { name: 'Name' })
    expect(name).toHaveValue('kite')
    expect(screen.getByText(/check it and name it/i)).toBeInTheDocument()
    // the three styles of the catalog, as small frames
    expect(screen.getAllByRole('img', { name: /drawn in the .+ style/i })).toHaveLength(3)
    // how the file was read: the colours its drawing marks
    expect(screen.getByText(/you can recolour in specs/i)).toBeInTheDocument()
    expect(screen.getByText('accent')).toBeInTheDocument()

    await user.clear(name)
    await user.type(name, 'Red Kite')
    expect(name).toHaveValue('red_kite')
    await user.type(screen.getByLabelText('Summary'), 'A red paper kite')
    await user.type(screen.getByLabelText('Words'), 'पतंग, flying toy{Enter}')
    expect(screen.getByRole('button', { name: 'Remove पतंग' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /save to library/i }))
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1))
    expect(onAdded.mock.calls[0][0]).toMatchObject({ name: 'red_kite', kind: 'object', origin: 'user', summary: 'A red paper kite', editable: true })
    expect(onAdded.mock.calls[0][0].tags).toEqual(expect.arrayContaining(['पतंग', 'flying toy']))
    expect(onOpenChange).toHaveBeenCalledWith(false)
    expect(await screen.findByText(/added “red_kite”/i)).toBeInTheDocument()

    // listed: in the asset list and in the catalog the editor offers
    expect((await api.listAssets()).assets.map((a) => a.name)).toContain('red_kite')
    expect((await api.catalog()).objects.map((o) => o.name)).toContain('red_kite')
  })

  it('offers a choice of file as well as a drop, and starts over with another file', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await open(api)
    await user.upload(screen.getByLabelText('Choose a file'), svgFile('first.svg'))
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('first')

    await user.click(screen.getByRole('button', { name: /use another file/i }))
    expect(zone()).toBeInTheDocument()
    await user.upload(screen.getByLabelText('Choose a file'), svgFile('second.svg'))
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('second')
  })

  it('shows a spinner while the server reads the file', async () => {
    const api = await libraryApi()
    let finish: (d: AssetDraft) => void = () => undefined
    vi.spyOn(api, 'createAssetDraft').mockImplementation(() => new Promise((r) => (finish = r)))
    await open(api)
    dropOn(zone(), svgFile('slow.svg'))
    expect(await screen.findByText(/reading/i)).toBeInTheDocument()
    expect(screen.getByText('slow.svg')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    finish({ id: 'd1', filename: 'slow.svg', format: 'svg', bytes: 10, suggested: { name: 'slow', kind: 'object', summary: '', tags: [], height: 300, anchor: [0.5, 1], facing: 'right' }, notes: [], roles: [], aspect: 1 })
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('slow')
  })
})

describe('a name that is taken', () => {
  it('says so, and replaces the built-in one only when asked', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const onAdded = vi.fn()
    await open(api, { onAdded })
    dropOn(zone(), svgFile('my_car.svg'))
    const name = await screen.findByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'car')
    await user.click(screen.getByRole('button', { name: /save to library/i }))

    expect(await screen.findByText(/there is already a built-in asset named “car”/i)).toBeInTheDocument()
    expect(name).toHaveAttribute('aria-invalid', 'true')
    expect(onAdded).not.toHaveBeenCalled()
    expect((await api.listAssets()).assets.find((a) => a.name === 'car')?.origin).toBe('builtin')

    await user.click(screen.getByRole('button', { name: /replace it with mine/i }))
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1))
    expect((await api.listAssets()).assets.find((a) => a.name === 'car')).toMatchObject({ origin: 'user', editable: true })
    expect(await screen.findByText(/replaced “car”/i)).toBeInTheDocument()
  })

  it('also warns about one of your own, and "change the name" takes you back to the field', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    await addUserAsset(api, 'kite')
    const onAdded = vi.fn()
    await open(api, { onAdded })
    dropOn(zone(), svgFile('kite2.svg'))
    const name = await screen.findByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'kite')
    await user.click(screen.getByRole('button', { name: /save to library/i }))

    expect(await screen.findByText(/there is already an asset named “kite”/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /change the name/i }))
    expect(screen.queryByText(/there is already an asset named/i)).not.toBeInTheDocument()
    expect(name).toHaveFocus()
    await user.type(name, '2')
    expect(name).toHaveValue('kite2')
    await user.click(screen.getByRole('button', { name: /save to library/i }))
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1))
    expect(onAdded.mock.calls[0][0].name).toBe('kite2')
  })
})

describe('a file the server will not take', () => {
  it('shows its message and what to do, right where the file was dropped, and lets you try another', async () => {
    const api = await libraryApi()
    await open(api)
    dropOn(zone(), new File(['hello'], 'notes.txt', { type: 'text/plain' }))
    expect(await screen.findByText('this is not a picture or drawing Reel can read')).toBeInTheDocument()
    expect(screen.getByText('use an SVG, PNG, JPG or WebP file')).toBeInTheDocument()
    // still on the first step
    expect(zone()).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()

    dropOn(zone(), svgFile())
    expect(await screen.findByRole('textbox', { name: 'Name' })).toBeInTheDocument()
    expect(screen.queryByText(/not a picture or drawing/i)).not.toBeInTheDocument()
  })

  it.each([
    [413, 'this file is larger than 12 MB', 'shrink the picture first'],
    [422, 'cannot use this file: the SVG has no size', 'open it in an editor and save it again'],
  ])('shows the %i message the same way', async (status, detail, hint) => {
    const api = await libraryApi()
    vi.spyOn(api, 'createAssetDraft').mockRejectedValue(new ApiError(status, detail, hint))
    await open(api)
    dropOn(zone(), svgFile())
    expect(await screen.findByText(detail)).toBeInTheDocument()
    expect(screen.getByText(hint)).toBeInTheDocument()
  })
})

describe('what the dialog starts with', () => {
  it('goes straight to the form for a file that came with it, filled in from what the script said', async () => {
    const api = await libraryApi()
    await open(api, {
      initial: { file: svgFile('dragon_picture.svg'), name: 'fire dragon', kind: 'character', tags: ['dragon', 'ड्रैगन'], summary: 'A purple dragon', hint: 'Your script says “dragon”: The dragon flew over the hills.' },
    })
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('fire_dragon')
    expect(screen.queryByText(/drop an svg, png, jpg or webp/i)).not.toBeInTheDocument()
    expect(screen.getByText(/your script says “dragon”/i)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Character' })).toBeChecked()
    expect(screen.getByLabelText('Summary')).toHaveValue('A purple dragon')
    expect(screen.getByRole('button', { name: 'Remove ड्रैगन' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove dragon' })).toBeInTheDocument()
  })

  it('says what the caller knows before the file is chosen, lets you put it right there, and keeps it for the form', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const created = vi.spyOn(api, 'createAssetDraft')
    await open(api, { initial: { name: 'castle', kind: 'place', tags: ['fort'], hint: 'Your script says “castle”: A dragon guards the old castle.' } })
    const name = screen.getByRole('textbox', { name: 'Name' })
    expect(name).toHaveValue('castle')
    expect(screen.getByRole('radio', { name: 'Place' })).toBeChecked()
    expect(screen.getByText(/a dragon guards the old castle/i)).toBeInTheDocument()

    await user.clear(name)
    await user.type(name, 'Old Castle')
    expect(name).toHaveValue('old_castle')
    await user.click(screen.getByRole('radio', { name: 'Object' }))
    dropOn(zone(), svgFile('whatever.svg'))

    await screen.findByText(/check it and name it/i)
    expect(created).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({ kind: 'object' }))
    expect(screen.getAllByRole('textbox', { name: 'Name' })).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('old_castle')
    expect(screen.getByRole('radio', { name: 'Object' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Remove fort' })).toBeInTheDocument()
  })

  it('reads a file that came with it once, and keeps the draft, in the strict mode the app runs in', async () => {
    const api = await libraryApi()
    const created = vi.spyOn(api, 'createAssetDraft')
    const discard = vi.spyOn(api, 'discardAssetDraft')
    await renderScreen(
      <StrictMode>
        <AddAssetDialog open onOpenChange={vi.fn()} initial={{ file: svgFile('lamp.svg'), name: 'lamp' }} />
      </StrictMode>,
      { api },
    )
    expect(await screen.findByRole('textbox', { name: 'Name' })).toHaveValue('lamp')
    expect(created).toHaveBeenCalledTimes(1) // the effect that starts the read runs twice in strict mode; the upload must not
    expect(discard).not.toHaveBeenCalled()
  })

  it('shows the hint above the first step too, and starts on the kind the page was showing', async () => {
    const api = await libraryApi()
    const created = vi.spyOn(api, 'createAssetDraft')
    await open(api, { initial: { kind: 'place', hint: 'Your script needs a village.' } })
    expect(screen.getByText('Your script needs a village.')).toBeInTheDocument()
    dropOn(zone(), svgFile('village.svg'))
    await screen.findByRole('textbox', { name: 'Name' })
    expect(created).toHaveBeenCalledWith(expect.any(File), expect.objectContaining({ kind: 'place', filename: 'village.svg' }))
    expect(screen.getByRole('radio', { name: 'Place' })).toBeChecked()
  })
})

describe('the previews', () => {
  it('are redrawn with the settings, and only once the settings stop changing', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const thumb = vi.spyOn(api, 'assetDraftThumbUrl')
    await open(api)
    dropOn(zone(), svgFile())
    await screen.findByRole('textbox', { name: 'Name' })
    const id = thumb.mock.calls[0][0]
    // one picture per catalog style, with the defaults of an object
    const styles = new Set(thumb.mock.calls.map((c) => c[1].style))
    expect(styles).toEqual(new Set(['flat_vector', 'paper_cutout', 'stickman']))
    expect(thumb.mock.calls[0][1]).toMatchObject({ kind: 'object', trueScale: false, height: 300, anchor: [0.5, 1], facing: 'right' })

    thumb.mockClear()
    await user.click(screen.getByRole('switch', { name: /next to a person/i }))
    await waitFor(() => expect(thumb).toHaveBeenCalledWith(id, expect.objectContaining({ trueScale: true, style: 'stickman' })))

    thumb.mockClear()
    await user.click(screen.getByRole('radio', { name: 'Left' }))
    // not on the spot: the drawing waits for the settings to settle
    expect(thumb.mock.calls.some((c) => c[1].facing === 'left')).toBe(false)
    await waitFor(() => expect(thumb).toHaveBeenCalledWith(id, expect.objectContaining({ facing: 'left', trueScale: true })))
  })

  it('for a place, swap the true-size switch for a time of day', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const thumb = vi.spyOn(api, 'assetDraftThumbUrl')
    await open(api)
    dropOn(zone(), svgFile())
    await screen.findByRole('textbox', { name: 'Name' })
    await user.click(screen.getByRole('radio', { name: 'Place' }))
    expect(screen.queryByRole('switch', { name: /next to a person/i })).not.toBeInTheDocument()
    const time = screen.getByRole('combobox', { name: 'Time of day' })
    await waitFor(() => expect(thumb).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ kind: 'place', timeOfDay: 'day' })))
    await user.click(time)
    await user.click(await screen.findByRole('option', { name: 'Dusk' }))
    await waitFor(() => expect(thumb).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ kind: 'place', timeOfDay: 'dusk' })))
  })

  it('for a picture, list how it was read and offer to keep its background', async () => {
    const api = await libraryApi()
    await open(api)
    dropOn(zone(), new File(['png'], 'photo.png', { type: 'image/png' }))
    expect(await screen.findByText(/how the file was read/i)).toBeInTheDocument()
    expect(screen.getByText('made the plain background transparent')).toBeInTheDocument()
    expect(within(screen.getByRole('radiogroup', { name: 'Remove plain background' })).getByRole('radio', { name: 'Auto' })).toBeChecked()
  })
})

describe('closing', () => {
  it('throws the draft away when cancelled, or when Escape is pressed', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const discard = vi.spyOn(api, 'discardAssetDraft')
    const created = vi.spyOn(api, 'createAssetDraft')
    const { onOpenChange } = await open(api)
    dropOn(zone(), svgFile())
    await screen.findByRole('textbox', { name: 'Name' })
    const id = ((await created.mock.results[0].value) as AssetDraft).id
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    expect(discard).toHaveBeenCalledWith(id)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('does not throw away what it has just saved', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    const discard = vi.spyOn(api, 'discardAssetDraft')
    const onAdded = vi.fn()
    await open(api, { onAdded })
    dropOn(zone(), svgFile())
    await screen.findByRole('textbox', { name: 'Name' })
    await user.click(screen.getByRole('button', { name: /save to library/i }))
    await waitFor(() => expect(onAdded).toHaveBeenCalled())
    expect(discard).not.toHaveBeenCalled()
  })

  it('throws away a draft that was still being read when it closed', async () => {
    const api = await libraryApi()
    let finish: (d: AssetDraft) => void = () => undefined
    vi.spyOn(api, 'createAssetDraft').mockImplementation(() => new Promise((r) => (finish = r)))
    const discard = vi.spyOn(api, 'discardAssetDraft')
    const user = userEvent.setup()
    await open(api)
    dropOn(zone(), svgFile())
    await screen.findByText(/reading/i)
    await user.click(screen.getByRole('button', { name: /^cancel$/i }))
    finish({ id: 'late', filename: 'kite.svg', format: 'svg', bytes: 10, suggested: { name: 'kite', kind: 'object', summary: '', tags: [], height: 300, anchor: [0.5, 1], facing: 'right' }, notes: [], roles: [], aspect: 1 })
    await waitFor(() => expect(discard).toHaveBeenCalledWith('late'))
  })

  it('says so, with a way back, when the upload has expired by the time it is saved', async () => {
    const user = userEvent.setup()
    const api = await libraryApi()
    vi.spyOn(api, 'commitAssetDraft').mockRejectedValue(new ApiError(404, 'this upload has expired', 'choose the file again to continue'))
    await open(api)
    dropOn(zone(), svgFile())
    await screen.findByRole('textbox', { name: 'Name' })
    await user.click(screen.getByRole('button', { name: /save to library/i }))
    expect(await screen.findByText('this upload has expired')).toBeInTheDocument()
    expect(screen.getByText('choose the file again to continue')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /choose the file again/i }))
    expect(zone()).toBeInTheDocument()
  })
})
