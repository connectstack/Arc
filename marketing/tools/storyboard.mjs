// The storyboard of the product video: resolves the narration timings and the recorded screenshots (with where the pointer was) into
// product-video/player/timeline.js (what the frame player draws) and product-video/audio_schedule.json (what the mixer places).
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.join(here, '..')
const pv = path.join(root, 'product-video')
const nar = JSON.parse(fs.readFileSync(path.join(pv, 'narration_timing.json'), 'utf8'))
const flows = {}
for (const k of 'ABCD') flows[k] = JSON.parse(fs.readFileSync(path.join(root, 'shots', `flow_${k}.json`), 'utf8'))
const nFrames = fs.readdirSync(path.join(pv, 'frames')).filter((f) => f.endsWith('.jpg')).length
const r = (n) => Math.round(n * 1000) / 1000

// ---------------------------------------------------------------------------------------------------------- when each line is said
const order = ['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8']
const gaps = { L1: 0.9, L2: 0.5, L3: 0.7, L4: 1.2, L5: 0.9, L6: 2.4, L7: 0.9, L8: 1.0 } // silence before each line
const T = {}
let t = 0
for (const id of order) {
  t += gaps[id]
  T[id] = { s: r(t), e: r(t + nar[id].dur) }
  t = T[id].e
}
const END = r(t + 2.4)
const lead = 0.2 // a scene starts a moment before its line
const sc = {
  s1: [0, r(T.L2.s - lead)],
  s2: [r(T.L2.s - lead), r(T.L3.s - lead)],
  s3: [r(T.L3.s - lead), r(T.L4.s - lead)],
  s4: [r(T.L4.s - lead), r(T.L5.s - lead)],
  s5: [r(T.L5.s - lead), r(T.L6.s - lead)],
  s6: [r(T.L6.s - lead), r(T.L7.s - lead)],
  s7: [r(T.L7.s - lead), r(T.L8.s - lead)],
  s8: [r(T.L8.s - lead), END],
}

// ---------------------------------------------------------------------------------------------------------- camera presets (CSS px of the 1440x900 screen, zoom)
const CAM = {
  whole: { cx: 720, cy: 450, z: 1.0 },
  overview: { cx: 720, cy: 450, z: 1.06 },
  cards: { cx: 722, cy: 250, z: 1.3 },
  wizard: { cx: 620, cy: 340, z: 1.5 },
  wizardLow: { cx: 620, cy: 500, z: 1.5 },
  look: { cx: 720, cy: 420, z: 1.3 },
  planning: { cx: 720, cy: 300, z: 1.8 },
  timeline: { cx: 800, cy: 720, z: 1.7 },
  inspector: { cx: 900, cy: 340, z: 1.6 },
  build: { cx: 500, cy: 400, z: 1.55 },
  buildTop: { cx: 330, cy: 260, z: 2.0 },
  dialog: { cx: 720, cy: 440, z: 1.45 },
  library: { cx: 720, cy: 470, z: 1.15 },
  search: { cx: 520, cy: 250, z: 1.6 },
  addAsset: { cx: 720, cy: 380, z: 1.4 },
}
const IMG = (file) => `../../shots/${file}`
function ui(flow, id, t0, cam, o = {}) {
  const s = flows[flow].find((x) => x.file === `${id}.png`)
  if (!s) throw new Error(`no shot ${flow}:${id}`)
  return { img: IMG(s.file), id, t0: r(t0), cam, pointer: s.pointer, ...o }
}
const pill = (text, t0, t1, o = {}) => ({ type: 'pill', text, t0: r(t0), t1: r(t1), x: 960, y: 940, ...o })

// ---------------------------------------------------------------------------------------------------------- the scenes
const scenes = []

// 1 the problem, said and shown as four words
scenes.push({
  id: 's1', kind: 'kinetic', t0: sc.s1[0], t1: sc.s1[1], noFadeIn: true,
  items: [
    { text: 'Making a short video means', y: 190, x: 200, size: 52, weight: 600, color: '#C9C4E4', t: 0.5 },
    { text: 'A timeline.', y: 300, x: 200, t: r(T.L1.s + 1.45) },
    { text: 'A voice-over.', y: 470, x: 200, t: r(T.L1.s + 2.45) },
    { text: 'An export.', y: 640, x: 200, t: r(T.L1.s + 3.35) },
    { text: 'And a lot of waiting.', y: 810, x: 200, color: '#FF8A4C', t: r(T.L1.s + 4.15) },
  ],
})

// 2 the promise
scenes.push({
  id: 's2', kind: 'promise', t0: sc.s2[0], t1: sc.s2[1],
  phones: ['../../deck/assets/phone-umbrella.jpg', '../../deck/assets/phone-hindi.jpg', '../../deck/assets/phone-sleep.jpg'],
})

// 3 a script becomes a reel
{
  const b = sc.s3[0]
  scenes.push({
    id: 's3', kind: 'ui', t0: sc.s3[0], t1: sc.s3[1],
    shots: [
      ui('A', 'A_01_projects', b + 0.0, CAM.cards, { pointer: { x: 900, y: 420 } }),
      ui('A', 'A_02_script_empty', b + 1.7, CAM.wizard),
      ui('A', 'A_03_script_hindi', b + 3.1, CAM.wizardLow),
      ui('A', 'A_04_look', b + 5.1, CAM.look),
      ui('A', 'A_05_planning', b + 6.1, CAM.planning),
      ui('A', 'A_07_studio_hindi', b + 7.3, CAM.whole),
    ],
    overlays: [
      pill('Paste a script', b + 0.3, b + 2.9),
      pill('English · Hindi · Hinglish', b + 3.2, b + 5.0),
      pill('Planned offline, in seconds', b + 5.2, b + 7.1),
      pill('Your first cut', b + 7.4, sc.s3[1] - 0.2),
    ],
  })
}

// 4 the studio
{
  const b = sc.s4[0]
  scenes.push({
    id: 's4', kind: 'ui', t0: sc.s4[0], t1: sc.s4[1],
    shots: [
      ui('B', 'B_01_studio_overview', b + 0.0, CAM.overview, { pointer: { x: 900, y: 330 } }),
      ui('B', 'B_02_studio_scene4', b + 2.7, CAM.timeline, { pointer: { x: 760, y: 700 }, click: false }),
      ui('B', 'B_03_studio_clip', b + 5.7, CAM.inspector, { pointer: { x: 1010, y: 300 }, click: false }),
    ],
    overlays: [
      pill('Live preview that plays with the voice', b + 0.3, b + 2.6),
      pill('A lane for every character', b + 3.0, b + 5.5),
      pill('An inspector for every detail', b + 5.9, sc.s4[1] - 0.2),
    ],
  })
}

// 5 build it by hand
{
  const b = sc.s5[0]
  const shots = [
    ui('C', 'C_01_scratch_dialog', b + 0.0, CAM.dialog, { pointer: { x: 720, y: 450 } }),
    ui('C', 'C_02_build_start', b + 0.95, CAM.build),
    ui('C', 'C_03_build_bg', b + 1.65, CAM.build),
    ui('C', 'C_04_build_bg_dusk', b + 2.3, CAM.build),
    ui('C', 'C_06_build_cast_kid', b + 3.1, CAM.build),
    ui('C', 'C_07_build_cast_dog', b + 3.9, CAM.build),
    ui('C', 'C_08_build_obj_search', b + 4.7, CAM.build),
    ui('C', 'C_09_build_obj', b + 5.4, CAM.build),
    ui('C', 'C_10_build_actions', b + 6.1, CAM.build),
    ui('C', 'C_12_build_sounds', b + 7.1, CAM.build),
    ui('C', 'C_14_build_words', b + 8.0, CAM.build),
    ui('C', 'C_15_build_scenes', b + 8.9, CAM.buildTop),
    ui('C', 'C_16_build_fit', b + 9.7, CAM.buildTop),
    ui('C', 'C_17_build_result', b + 10.6, CAM.whole, { pointer: { x: 1000, y: 420 }, click: false }),
  ]
  const at = (id) => shots.find((s) => s.id === id).t0
  scenes.push({
    id: 's5', kind: 'ui', t0: sc.s5[0], t1: sc.s5[1], shots,
    chips: {
      labels: ['Background', 'Characters', 'Objects', 'Actions', 'Sounds', 'Words'], y: 34, t0: r(at('C_02_build_start') - 0.1), t1: r(at('C_16_build_fit') + 0.6),
      steps: [
        { t0: at('C_03_build_bg'), idx: 0 }, { t0: at('C_06_build_cast_kid'), idx: 1 }, { t0: at('C_08_build_obj_search'), idx: 2 },
        { t0: at('C_10_build_actions'), idx: 3 }, { t0: at('C_12_build_sounds'), idx: 4 }, { t0: at('C_14_build_words'), idx: 5 }, { t0: at('C_15_build_scenes'), idx: 99 },
      ],
    },
    overlays: [
      pill('No script? Build it by hand', b + 0.1, b + 1.8, { dot: '#B3A8FF' }),
      pill('Every pick is one undo step', b + 5.0, b + 7.0, { y: 960 }),
      pill('The meter keeps it within 45–60 s', at('C_15_build_scenes') + 0.1, at('C_17_build_result') - 0.1, { y: 960 }),
      pill('Built by hand in a few clicks', at('C_17_build_result') + 0.2, sc.s5[1] - 0.2, { dot: '#6BE3A5' }),
    ],
  })
}

// 6 the library
{
  const b = sc.s6[0]
  scenes.push({
    id: 's6', kind: 'ui', t0: sc.s6[0], t1: sc.s6[1],
    shots: [
      ui('D', 'D_02_library_characters', b + 0.0, CAM.library, { pointer: { x: 900, y: 300 } }),
      ui('D', 'D_03_library_search_hindi', b + 3.2, CAM.search),
      ui('D', 'D_06_library_add', b + 5.0, CAM.addAsset),
    ],
    overlays: [
      { type: 'stat', big: '39', label: 'characters', x: 1330, y: 120, t0: r(T.L6.s + 1.15), t1: r(T.L6.s + 4.65) },
      { type: 'stat', big: '72', label: 'objects', x: 1330, y: 400, t0: r(T.L6.s + 2.2), t1: r(T.L6.s + 4.65) },
      { type: 'stat', big: '20', label: 'places', x: 1330, y: 680, t0: r(T.L6.s + 3.2), t1: r(T.L6.s + 4.65) },
      pill('Search in Hindi: बिल्ली finds the cat', b + 3.4, b + 4.9, { x: 700 }),
      pill('Add your own artwork', b + 5.2, sc.s6[1] - 0.15, { x: 700 }),
    ],
  })
}

// 7 export: the dialog, then the real render playing
{
  const b = sc.s7[0]
  const split = r(b + 2.6)
  scenes.push({
    id: 's7a', kind: 'ui', t0: sc.s7[0], t1: split,
    shots: [
      ui('C', 'C_18_export_dialog', b + 0.0, CAM.dialog, { pointer: { x: 1500, y: 330 }, click: false }),
      ui('C', 'C_19_export_done', b + 1.35, CAM.dialog),
    ],
    overlays: [pill('Export: Full HD, vertical MP4', b + 0.2, b + 2.5, { dot: '#B3A8FF' })],
  })
  scenes.push({
    id: 's7b', kind: 'phone', t0: split, t1: sc.s7[1],
    frames: Array.from({ length: nFrames }, (_, i) => `../frames/f${String(i + 1).padStart(4, '0')}.jpg`), fps: 30, playFrom: 0.2,
    rows: [
      { big: '80 s', size: 150, label: 'Full HD render of a 50 s reel on a laptop', y: 190, t: r(T.L7.s + 2.7) },
      { big: 'Repeatable', size: 120, label: 'Same script and settings, same video', y: 440, color: '#B3A8FF', t: r(T.L7.s + 5.6) },
      { big: '0 uploads', size: 150, label: 'Nothing leaves your machine unless you choose', y: 690, color: '#FF8A4C', t: r(T.L7.s + 8.4) },
    ],
  })
}

// 8 the offer
scenes.push({
  id: 's8', kind: 'outro', t0: sc.s8[0], t1: sc.s8[1],
  chips: [
    { text: 'Product', bg: '#4D3DD1', t: r(T.L8.s + 2.8) },
    { text: 'Service', bg: '#6B5CE7', t: r(T.L8.s + 3.9) },
    { text: 'Platform', bg: '#FF8A4C', color: '#15122B', t: r(T.L8.s + 5.1) },
  ],
})

const timeline = { duration: END, fps: 30, scenes }
fs.writeFileSync(path.join(pv, 'player', 'timeline.js'), `window.TIMELINE = ${JSON.stringify(timeline, null, 1)}\n`)

// ---------------------------------------------------------------------------------------------------------- the sound
const sfx = []
for (const s of scenes) {
  if (s.kind === 'ui')
    s.shots.forEach((x, i) => {
      if (i > 0 && x.pointer && x.click !== false) sfx.push({ name: 'click', t: r(x.t0 - 0.1), gain: -9 })
    })
}
sfx.push({ name: 'sparkle', t: r(sc.s2[0] + 0.15), gain: -14 })
for (const s of scenes.slice(1)) sfx.push({ name: 'whoosh', t: r(s.t0 - 0.05), gain: -17 })
sfx.push({ name: 'success', t: r(sc.s5[0] + 10.7), gain: -12 })
sfx.push({ name: 'tada', t: r(sc.s8[0] + 0.25), gain: -13 })
sfx.push({ name: 'pop', t: r(T.L6.s + 1.15), gain: -12 }, { name: 'pop', t: r(T.L6.s + 2.2), gain: -12 }, { name: 'pop', t: r(T.L6.s + 3.2), gain: -12 })
sfx.sort((a, b) => a.t - b.t)
const schedule = { duration: END, vo: order.map((id) => ({ id, file: nar[id].file, t: T[id].s })), sfx, T, scenes: Object.fromEntries(Object.entries(sc)) }
fs.writeFileSync(path.join(pv, 'audio_schedule.json'), JSON.stringify(schedule, null, 1))
console.log('duration', END, 's;', scenes.length, 'scenes;', sfx.length, 'sound effects;', nFrames, 'frames of the sample render')
for (const [k, v] of Object.entries(sc)) console.log(k, v.join(' – '))
