// Records the product tour: drives the real Reel Studio (a demo workspace on :8768) in a headless browser and saves a crisp 2x screenshot
// after every step, with where the pointer was. The stills are used in the sales deck; the sequence, with the pointer, is the footage of the
// product video. Usage: node record.mjs <flow letters, e.g. ABC> (each flow writes shots/<flow>_NN_name.png and shots/flow_<flow>.json)
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { launch, sleep } from './cdp.mjs'

const BASE = process.env.REEL_URL ?? 'http://127.0.0.1:8768'
const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'shots')
mkdirSync(OUT, { recursive: true })
const wanted = (process.argv[2] ?? 'A').toUpperCase().split('')

const J = JSON.stringify
const btn = (text) => `[...document.querySelectorAll('button')].find((b) => b.textContent.trim().startsWith(${J(text)}))`
const btnHas = (text) => `[...document.querySelectorAll('button')].find((b) => b.textContent.includes(${J(text)}))`
const label = (l) => `document.querySelector('[aria-label=${J(l)}]')`
const tab = (name) => `[...document.querySelectorAll('[role=tab]')].find((t) => t.textContent.trim() === ${J(name)})`
const step = (name) => `[...document.querySelectorAll('button[aria-expanded]')].find((b) => b.textContent.trim().startsWith(${J(name)}))`
const inGroup = (group, text) => `[...document.querySelectorAll('[role=group][aria-label=${J(group)}] button')].find((b) => b.textContent.trim().toLowerCase().startsWith(${J(text.toLowerCase())}))`

const api = async (path, init) => (await fetch(BASE + path, init)).json()

async function main() {
  const page = await launch({ width: 1440, height: 900, dpr: 2 })
  let flow = ''
  let log = []
  let pointer = { x: 720, y: 450 }
  let location_is_studio = false

  /** Hide what is only this machine's (the workspace path in the status bar), and let images and the preview settle. */
  async function tidy() {
    await page.eval(`(() => {
      const e = [...document.querySelectorAll('*')].find((n) => n.children.length === 0 && /^\\/private\\//.test(n.textContent)); if (e) e.textContent = '~/Reels/workspace'
      window.scrollTo(0, 0); for (const n of document.querySelectorAll('body, #root, #root *')) if (n.scrollTop > 0 && getComputedStyle(n).overflowY === 'hidden') n.scrollTop = 0
    })()`)
  }
  /** Wait until what shows is saved (the autosave runs a moment after an edit). */
  async function saved() {
    await page.waitFor(`/Saved/.test(document.body.innerText) && !/Unsaved changes/.test(document.body.innerText)`, 8000).catch(() => {})
  }
  /** Turn the stage's handles off: stills of a reel look better without them. */
  async function handlesOff() {
    const on = await page.eval(`document.querySelector('button[aria-label="Character and object handles"]')?.getAttribute('aria-pressed')`)
    if (on === 'true') await click(label('Character and object handles'))
  }
  async function images(timeout = 20000) {
    await page.waitFor(`[...document.images].filter((i) => { const r = i.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth }).every((i) => i.complete && i.naturalWidth > 0)`, timeout).catch(() => console.log('  (some images did not finish)'))
  }
  async function snap(name, { wait = 500, note = '' } = {}) {
    await images()
    if (location_is_studio) await saved()
    await sleep(wait)
    await tidy()
    const file = `${flow}_${String(log.length + 1).padStart(2, '0')}_${name}.png`
    await page.shot(join(OUT, file))
    log.push({ id: name, file, pointer: { ...pointer }, note })
    console.log(`  ${file}`)
  }
  async function click(expression, { settle = 350 } = {}) {
    const p = await page.clickEl(expression)
    pointer = { x: p.x, y: p.y }
    await sleep(settle)
    return p
  }
  async function clickText(text, opts) {
    return click(btn(text), opts)
  }
  async function begin(name, url, ready) {
    flow = name
    log = []
    console.log(`flow ${name}`)
    location_is_studio = false
    if (url) await page.goto(BASE + url, ready)
  }
  async function end() {
    writeFileSync(join(OUT, `flow_${flow}.json`), JSON.stringify(log, null, 1))
  }
  /** Move the playhead to a time in the reel by clicking the timeline's ruler. */
  async function seek(t) {
    await page.eval(`(() => { const c = document.querySelector('section[aria-label=Timeline] .overflow-auto'); if (c) c.scrollLeft = 0 })()`)
    await sleep(200)
    const zoom = Number(await page.eval(`document.querySelector('input[aria-label="Timeline zoom"]')?.value ?? 22`)) || 22
    const b = await page.box(`document.querySelector('section[aria-label=Timeline] .cursor-col-resize')`)
    const x = b.x + 1 + t * zoom
    await page.click(x, b.y + b.h / 2)
    pointer = { x, y: b.y + b.h / 2 }
    await sleep(900)
  }
  /** Move the playhead forward by frames with the transport's button. */
  async function stepFrames(n) {
    const b = await page.box(label('Forward one frame'))
    for (let i = 0; i < n; i++) await page.click(b.cx, b.cy)
    pointer = { x: b.cx, y: b.cy }
    await sleep(1200)
  }
  async function stageReady() {
    await page.waitFor(`document.querySelector('canvas[aria-label="Preview of the reel at the playhead"]') !== null`)
    await sleep(2200)
  }
  const ids = async () => (await api('/api/projects')).map((p) => p.id)

  // ----------------------------------------------------------------------------------------------- A: from a script
  if (wanted.includes('A')) {
    const before = await ids()
    await begin('A', '/', "document.querySelector('h1,h2') !== null")
    await sleep(1200)
    await snap('projects', { wait: 800 })
    await clickText('From a script')
    await page.waitFor(`document.querySelector('textarea[aria-label="Your script"]') !== null`)
    await snap('script_empty')
    await click(btnHas('खोया हुआ छाता'))
    await page.waitFor(`document.querySelector('textarea[aria-label="Your script"]').value.length > 50`)
    await page.waitFor(`/library check/i.test(document.body.innerText)`, 8000).catch(() => console.log('  (no library check panel)'))
    await sleep(900)
    await snap('script_hindi', { note: 'Hindi script with the library check' })
    await clickText('Choose a look')
    await page.waitFor(`document.querySelector('[role=radiogroup][aria-label="Visual style"]') !== null`)
    await snap('look')
    await clickText('Plan my reel', { settle: 250 })
    await snap('planning', { wait: 100 })
    await page.waitFor(`/studio|your reel is ready|open in the studio/i.test(location.pathname + document.body.innerText)`, 20000).catch(() => {})
    await sleep(1500)
    await snap('planned', { note: 'Planned' })
    await page.waitFor(`location.pathname.startsWith('/p/')`, 20000).catch(() => console.log('  (did not reach the studio by itself)'))
    await stageReady()
    location_is_studio = true
    await handlesOff()
    await click(btn('Fit all')).catch(() => {})
    await seek(9)
    await sleep(1800)
    await snap('studio_hindi', { wait: 800 })
    const made = (await ids()).filter((i) => !before.includes(i))
    console.log('  created:', made.join(', '))
    writeFileSync(join(OUT, 'created_A.json'), JSON.stringify(made))
    await end()
  }

  // ----------------------------------------------------------------------------------------------- B: the studio on a finished reel
  if (wanted.includes('B')) {
    await begin('B', '/p/the-lost-umbrella', `document.querySelector('canvas[aria-label="Preview of the reel at the playhead"]') !== null`)
    await stageReady()
    location_is_studio = true
    await handlesOff()
    await click(btn('Fit all'))
    await snap('studio_overview', { wait: 1200 })
    await click(`document.querySelectorAll('button[aria-label^="Reorder scene"]')[3].parentElement.querySelector('button.flex-1')`)
    await sleep(1800)
    await snap('studio_scene4', { wait: 800 })
    await stepFrames(96)
    // an action clip of this scene (Mia's jump): the inspector shows its settings, the stage the moment it happens
    await click(`document.querySelector('section[aria-label=Timeline] [data-clip="a-3-0-0"]')`).catch(() => console.log('  (no action clip found)'))
    await sleep(1300)
    await snap('studio_clip', { wait: 900 })
    await click(tab('JSON'))
    await page.waitFor(`document.querySelector('.cm-editor') !== null`, 15000).catch(() => console.log('  (no JSON editor)'))
    await sleep(1500)
    await snap('studio_json', { wait: 800 })
    await click(tab('Inspector'))
    await end()
  }


  // ----------------------------------------------------------------------------------------------- C: build from scratch
  if (wanted.includes('C')) {
    await fetch(BASE + '/api/projects/beach-birthday', { method: 'DELETE' }).catch(() => {})
    const before = await ids()
    await begin('C', '/', "document.querySelector('h1,h2') !== null")
    await sleep(1000)
    await click(btn('Build from scratch'))
    await page.waitFor(`document.querySelector('[role=dialog] input') !== null`)
    await sleep(500)
    await page.type('Beach birthday')
    await sleep(300)
    await snap('scratch_dialog', { wait: 900 })
    await click(btn('Start building'), { settle: 600 })
    await page.waitFor(`location.pathname.startsWith('/p/') && document.querySelector('[role=region][aria-label="Build a reel from the library"]') !== null`, 20000)
    await stageReady()
    location_is_studio = true
    await handlesOff()
    await snap('build_start', { wait: 800 })
    // 1 background
    await click(inGroup('Backgrounds', 'beach'))
    await sleep(2000)
    await snap('build_bg', { wait: 600 })
    await click(`[...document.querySelectorAll('[role=radio]')].find((r) => r.textContent.trim() === 'Dusk')`)
    await sleep(2000)
    await snap('build_bg_dusk', { wait: 600 })
    // 2 characters
    await click(btn('Next: Characters'))
    await sleep(600)
    await snap('build_cast_open', { wait: 1500 })
    await click(inGroup('Characters', 'kid'))
    await sleep(2000)
    await snap('build_cast_kid', { wait: 600 })
    await click(inGroup('Characters', 'dog'))
    await sleep(2200)
    await snap('build_cast_dog', { wait: 600 })
    // 3 objects
    await click(btn('Next: Objects'))
    await sleep(700)
    await page.type('cake')
    await sleep(500)
    await snap('build_obj_search', { wait: 500 })
    await click(`[...document.querySelectorAll('[role=list][aria-label="Library objects"] button')].find((b) => b.textContent.trim().toLowerCase().startsWith('cake'))`)
    await sleep(1800)
    await page.eval(`document.querySelector('input[aria-label="Find an object"]').select()`)
    await page.type('balloon')
    await sleep(500)
    await click(`[...document.querySelectorAll('[role=list][aria-label="Library objects"] button')].find((b) => b.textContent.trim().toLowerCase().startsWith('balloon'))`)
    await sleep(2000)
    await snap('build_obj', { wait: 600 })
    // 4 actions
    await click(step('Actions'))
    await sleep(700)
    await click(label('Add wave for Kid'))
    await sleep(500)
    await click(label('Add laugh for Kid'))
    await sleep(500)
    await click(inGroup('Who does it', 'dog'))
    await click(label('Add jump for Dog'))
    await sleep(1800)
    await snap('build_actions', { wait: 600 })
    // 5 sounds
    await click(step('Sounds'))
    await sleep(700)
    await click(label('Music'))
    await sleep(500)
    await snap('build_music_menu', { wait: 400 })
    await click(`[...document.querySelectorAll('[role=option]')].find((o) => o.textContent.trim().startsWith('Playful'))`)
    await sleep(500)
    await click(label('Add applause'))
    await sleep(900)
    await snap('build_sounds', { wait: 500 })
    // 6 words
    await click(step('Words'))
    await sleep(700)
    await click(label('What is said'))
    await page.type('Happy birthday, Bruno!')
    await sleep(300)
    await snap('build_words_typed', { wait: 400 })
    await click(btn('Add line'))
    await sleep(1500)
    await snap('build_words', { wait: 500 })
    // more scenes and the length
    await click(btn('Scene like this'), { settle: 1200 })
    await click(btn('Scene like this'), { settle: 1200 })
    await snap('build_scenes', { wait: 800 })
    await click(btn('Fit to 50 s'), { settle: 1500 })
    await snap('build_fit', { wait: 800 })
    // back to the first scene, a moment in
    await click(label('Previous scene'))
    await click(label('Previous scene'))
    await sleep(1500)
    await click(btn('Fit all')).catch(() => {})
    await seek(2.2)
    await sleep(1800)
    await snap('build_result', { wait: 1200 })
    // export
    await click(btn('Render'), { settle: 900 })
    await page.waitFor(`document.querySelector('[role=dialog]') !== null`)
    await sleep(1200)
    await snap('export_dialog', { wait: 800 })
    await click(`[...document.querySelectorAll('[role=dialog] button')].find((b) => /^Render Draft/.test(b.textContent.trim()))`, { settle: 800 })
    await page.waitFor(`/video is ready/i.test(document.body.innerText)`, 120000).catch(() => console.log('  (the draft did not finish)'))
    await sleep(1500)
    await snap('export_done', { wait: 800 })
    const made = (await ids()).filter((i) => !before.includes(i))
    console.log('  created:', made.join(', '))
    writeFileSync(join(OUT, 'created_C.json'), JSON.stringify(made))
    await end()
  }

  // ----------------------------------------------------------------------------------------------- D: the library
  if (wanted.includes('D')) {
    await begin('D', '/library', `document.querySelector('h1,h2') !== null`)
    await sleep(1500)
    await snap('library_start', { wait: 1500 })
    await click(`[...document.querySelectorAll('[role=tab]')].find((t) => t.textContent.trim() === 'Characters')`)
    await sleep(1500)
    await snap('library_characters', { wait: 2000 })
    await click(`document.querySelector('input[aria-label="Search the library"]')`)
    await page.type('बिल्ली')
    await sleep(900)
    await snap('library_search_hindi', { wait: 1500 })
    await page.eval(`document.querySelector('input[aria-label="Search the library"]').select()`)
    await page.key('Backspace')
    await click(`[...document.querySelectorAll('[role=tab]')].find((t) => t.textContent.trim() === 'Backgrounds')`)
    await sleep(1500)
    await snap('library_places', { wait: 2500 })
    await click(`[...document.querySelectorAll('[role=tab]')].find((t) => t.textContent.trim() === 'Objects')`)
    await sleep(1500)
    await snap('library_objects', { wait: 2000 })
    await click(btn('Add asset'))
    await page.waitFor(`document.querySelector('[role=dialog]') !== null`)
    await sleep(700)
    await snap('library_add', { wait: 600 })
    await end()
  }

  // ----------------------------------------------------------------------------------------------- E: voice and audio
  if (wanted.includes('E')) {
    await begin('E', '/p/the-lost-umbrella/audio', `document.querySelector('h1,h2') !== null`)
    await sleep(2500)
    await snap('audio', { wait: 1500 })
    await end()
  }

  await page.close()
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
