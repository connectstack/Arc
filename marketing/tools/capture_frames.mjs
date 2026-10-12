// Draws the product video's frames with the frame player (headless Chrome) and encodes them.
//   node capture_frames.mjs test <t> <t> ...        saves product-video/test/t_<t>.png at 1920x1080
//   node capture_frames.mjs render <out.mp4> [N]    renders every frame (N parallel workers) into a silent H.264 file
import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { launch, sleep } from './cdp.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const pv = join(here, '..', 'product-video')
const PLAYER = pathToFileURL(join(pv, 'player', 'index.html')).href
const [mode, ...args] = process.argv.slice(2)

async function open(port) {
  const page = await launch({ width: 1920, height: 1080, dpr: 1, port })
  await page.goto(PLAYER, 'window.ready !== undefined')
  await page.eval('window.ready')
  return page
}

if (mode === 'test') {
  mkdirSync(join(pv, 'test'), { recursive: true })
  const page = await open(9340)
  for (const t of args.map(Number)) {
    await page.eval(`window.seek(${t})`)
    await sleep(60)
    await page.shot(join(pv, 'test', `t_${String(t).replace('.', '_')}.png`))
    console.log('t =', t)
  }
  await page.close()
} else if (mode === 'segment') {
  // internal: one worker renders frames [from, to) into a file
  const [out, from, to, port] = args
  const fps = 30
  const page = await open(Number(port))
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-', '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-pix_fmt', 'yuv420p', '-g', '30', '-r', String(fps), out], { stdio: ['pipe', 'inherit', 'inherit'] })
  for (let f = Number(from); f < Number(to); f++) {
    await page.eval(`window.seek(${(f / fps).toFixed(4)})`)
    const r = await page.send('Page.captureScreenshot', { format: 'jpeg', quality: 93, fromSurface: true })
    const buf = Buffer.from(r.data, 'base64')
    if (!ff.stdin.write(buf)) await new Promise((res) => ff.stdin.once('drain', res))
    if ((f - Number(from)) % 60 === 0) console.log(`  frame ${f} of ${to}`)
  }
  ff.stdin.end()
  await new Promise((res) => ff.on('close', res))
  await page.close()
} else if (mode === 'render') {
  const out = args[0]
  const workers = Number(args[1] ?? 4)
  const fps = 30
  const total = Math.round(JSON.parse(readFileSync(join(pv, 'audio_schedule.json'), 'utf8')).duration * fps)
  const per = Math.ceil(total / workers)
  const parts = []
  const procs = []
  for (let w = 0; w < workers; w++) {
    const from = w * per
    const to = Math.min(total, from + per)
    if (from >= to) continue
    const part = join(pv, `part_${w}.mp4`)
    parts.push(part)
    procs.push(
      new Promise((res, rej) => {
        const p = spawn('node', [fileURLToPath(import.meta.url), 'segment', part, String(from), String(to), String(9350 + w)], { stdio: 'inherit' })
        p.on('close', (c) => (c === 0 ? res() : rej(new Error(`worker ${w} failed (${c})`))))
      }),
    )
  }
  await Promise.all(procs)
  const list = join(pv, 'parts.txt')
  writeFileSync(list, parts.map((p) => `file '${p}'`).join('\n'))
  const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', out], { stdio: 'inherit' })
  if (r.status !== 0) throw new Error('concat failed')
  for (const p of parts) rmSync(p, { force: true })
  rmSync(list, { force: true })
  console.log('wrote', out, `(${total} frames)`)
}
