// The product video as a function of time: seek(t) draws the frame at t seconds (scenes, camera moves over real screenshots of Reel Studio,
// a pointer with clicks, callouts). A capture script steps t at 30 fps and saves each frame; nothing here runs in real time.
'use strict'
const TL = window.TIMELINE
const stage = document.getElementById('stage')
const S0 = 1680 / 1440 // a window at zoom 1 is 1680 px wide on the 1920 px canvas
const FONT = "'Avenir Next', 'Helvetica Neue', Arial, sans-serif"
const COL = { ink: '#15122B', ink2: '#211C45', paper: '#F6F2EA', violet: '#6B5CE7', violetD: '#4D3DD1', violetL: '#B3A8FF', orange: '#FF8A4C', muted: '#C9C4E4' }

const clamp = (v, a, b) => Math.min(b, Math.max(a, v))
const lerp = (a, b, t) => a + (b - a) * t
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)
const easeOut = (t) => 1 - Math.pow(1 - t, 3)
const easeOutBack = (t, s = 1.3) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2)
const seg = (t, a, b) => clamp((t - a) / (b - a), 0, 1)
const px = (n) => `${n}px`

function el(tag, css, parent, text) {
  const e = document.createElement(tag)
  if (css) Object.assign(e.style, css)
  if (text != null) e.textContent = text
  ;(parent || stage).appendChild(e)
  return e
}

function loadImg(src) {
  const im = new Image()
  im.decoding = 'async'
  im.src = src
  return im
}
const decoded = (im) => im.__p || (im.__p = (im.decode ? im.decode() : Promise.resolve()).catch(() => {}))

// ------------------------------------------------------------------------------------------------------ background
const bg = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
function updateBg(t) {
  const a = Math.sin(t * 0.21) * 0.5 + 0.5
  const b = Math.cos(t * 0.16) * 0.5 + 0.5
  bg.style.background = `radial-gradient(900px 700px at ${lerp(12, 38, a)}% ${lerp(18, 62, b)}%, rgba(107,92,231,0.38), rgba(107,92,231,0) 66%), radial-gradient(1000px 800px at ${lerp(88, 60, b)}% ${lerp(82, 40, a)}%, rgba(255,138,76,0.13), rgba(255,138,76,0) 62%), ${COL.ink}`
}

// ------------------------------------------------------------------------------------------------------ small pieces
function logo(parent, size, x, y) {
  const d = el('div', { position: 'absolute', left: px(x), top: px(y), width: px(size), height: px(size), borderRadius: px(size * 0.25), background: COL.violet, boxShadow: '0 10px 30px rgba(107,92,231,0.45)' }, parent)
  d.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}"><path d="M9 6.8 L9 17.2 L17.6 12 Z" fill="#F6F2EA" stroke="#F6F2EA" stroke-width="1.6" stroke-linejoin="round"/></svg>`
  return d
}

function pill(text, o) {
  const d = el('div', {
    position: 'absolute', left: px(o.x), top: px(o.y), display: 'none', whiteSpace: 'nowrap', padding: `${o.pad ?? 18}px ${o.padX ?? 38}px`, borderRadius: '60px',
    background: 'rgba(21,18,43,0.88)', border: '1.5px solid rgba(255,255,255,0.2)', color: COL.paper, fontSize: px(o.size ?? 40), fontWeight: 700,
    letterSpacing: '-0.3px', boxShadow: '0 18px 50px rgba(0,0,0,0.45)',
  }, o.parent)
  d.innerHTML = `<span style="display:inline-block;width:14px;height:14px;border-radius:7px;background:${o.dot ?? COL.orange};margin-right:20px;vertical-align:middle"></span><span style="vertical-align:middle">${text}</span>`
  return d
}

// ------------------------------------------------------------------------------------------------------ overlays shared by scenes
function makeOverlay(o, parent) {
  const { t0, t1 } = o
  const place = (d, anchor) => {
    d.__anchor = anchor
  }
  let d
  if (o.type === 'pill') {
    d = pill(o.text, { ...o, parent })
    d.__center = o.anchor !== 'left'
  } else if (o.type === 'stat') {
    d = el('div', { position: 'absolute', left: px(o.x), top: px(o.y), display: 'none', whiteSpace: 'nowrap', padding: '20px 46px 26px', borderRadius: '30px', background: 'rgba(21,18,43,0.9)', border: '1.5px solid rgba(255,255,255,0.18)', boxShadow: '0 24px 60px rgba(0,0,0,0.5)' }, parent)
    d.innerHTML = `<div style="font-size:${o.size ?? 150}px;font-weight:800;line-height:1;letter-spacing:-4px;color:${o.color ?? COL.paper}">${o.big}</div><div style="margin-top:6px;font-size:${o.labelSize ?? 38}px;font-weight:600;color:${o.labelColor ?? COL.muted}">${o.label}</div>`
  } else if (o.type === 'badge') {
    d = el('div', { position: 'absolute', left: px(o.x), top: px(o.y), display: 'none', whiteSpace: 'nowrap', padding: '14px 28px', borderRadius: '16px', background: o.bg ?? COL.violet, color: COL.paper, fontSize: px(o.size ?? 34), fontWeight: 800 }, parent, o.text)
  }
  return {
    update(t) {
      const on = t >= t0 && t <= t1
      d.style.display = on ? 'block' : 'none'
      if (!on) return
      const a = seg(t, t0, t0 + 0.4)
      const out = 1 - seg(t, t1 - 0.3, t1)
      d.style.opacity = String(Math.min(a, out))
      const rise = (1 - easeOutBack(a, 0.9)) * (o.rise ?? 30)
      d.style.transform = `${d.__center ? 'translateX(-50%) ' : ''}translateY(${rise}px)`
    },
  }
}

function makeChips(c, parent) {
  const row = el('div', { position: 'absolute', left: '0px', top: px(c.y ?? 34), width: '1920px', display: 'none', justifyContent: 'center', gap: '14px' }, parent)
  const chips = c.labels.map((label, i) => {
    const d = el('div', { padding: '12px 26px', borderRadius: '40px', fontSize: '30px', fontWeight: 700, letterSpacing: '-0.2px', whiteSpace: 'nowrap', border: '1.5px solid rgba(255,255,255,0.18)' }, row)
    d.textContent = `${i + 1}  ${label}`
    return d
  })
  return {
    update(t) {
      const on = t >= c.t0 && t <= c.t1
      row.style.display = on ? 'flex' : 'none'
      if (!on) return
      row.style.opacity = String(Math.min(seg(t, c.t0, c.t0 + 0.35), 1 - seg(t, c.t1 - 0.3, c.t1)))
      let active = -1
      for (const s of c.steps) if (t >= s.t0) active = s.idx
      chips.forEach((d, i) => {
        const done = active === 99 || i < active
        const now = i === active
        d.style.background = now ? COL.violet : done ? 'rgba(107,92,231,0.55)' : 'rgba(21,18,43,0.82)'
        d.style.color = now || done ? COL.paper : 'rgba(246,242,234,0.55)'
        d.style.transform = now ? 'scale(1.07)' : 'scale(1)'
        d.style.borderColor = now ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.18)'
        d.textContent = `${done ? '✓' : i + 1}  ${c.labels[i]}`
      })
    },
  }
}

// ------------------------------------------------------------------------------------------------------ UI scenes: a camera over screenshots
function makeCursor(parent) {
  const c = el('div', { position: 'absolute', left: '0px', top: '0px', width: '0px', height: '0px', transformOrigin: '0 0', display: 'none' }, parent)
  c.innerHTML = `<svg width="40" height="40" viewBox="0 0 24 24" style="position:absolute;left:-8px;top:-5px;filter:drop-shadow(0 3px 5px rgba(0,0,0,.55))"><path d="M5 3 L5 19.5 L9.2 15.6 L12 21 L14.4 19.9 L11.7 14.6 L17.5 14.4 Z" fill="#fff" stroke="#15122B" stroke-width="1.4" stroke-linejoin="round"/></svg>`
  const rip = el('div', { position: 'absolute', left: '0px', top: '0px', width: '64px', height: '64px', marginLeft: '-32px', marginTop: '-32px', borderRadius: '50%', border: '5px solid rgba(255,255,255,0.95)', boxShadow: '0 0 0 3px rgba(107,92,231,0.55)', display: 'none', transformOrigin: '50% 50%' }, parent)
  return { c, rip }
}

function shotIndex(shots, t) {
  let k = 0
  for (let i = 0; i < shots.length; i++) if (t >= shots[i].t0) k = i
  return k
}

function uiScene(def) {
  const root = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
  const win = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1440px', height: '900px', transformOrigin: '0 0', overflow: 'hidden', background: '#0b0d12', boxShadow: '0 40px 120px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.1)' }, root)
  const imgs = def.shots.map((s) => {
    const im = loadImg(s.img)
    Object.assign(im.style, { position: 'absolute', left: '0px', top: '0px', width: '1440px', height: '900px', display: 'none' })
    win.appendChild(im)
    return im
  })
  const cur = makeCursor(win)
  const overlays = (def.overlays || []).map((o) => makeOverlay(o, root))
  const chips = def.chips ? makeChips(def.chips, root) : null
  const shots = def.shots

  function camAt(t) {
    const k = shotIndex(shots, t)
    const to = shots[k].cam
    const from = k > 0 ? shots[k - 1].cam : to
    const p = k > 0 ? ease(seg(t, shots[k].t0 - 0.2, shots[k].t0 + (shots[k].camDur ?? 0.9))) : 1
    return { cx: lerp(from.cx, to.cx, p), cy: lerp(from.cy, to.cy, p), z: lerp(from.z, to.z, p) }
  }

  function pointerAt(t) {
    // the pointer travels to each shot's click point just before that shot's picture appears
    let pos = shots[0].pointer || { x: 720, y: 450 }
    for (let i = 0; i < shots.length; i++) {
      const s = shots[i]
      if (!s.pointer) continue
      const ts = s.t0 - 0.7
      const te = s.t0 - 0.1
      if (i === 0) {
        pos = s.pointer
        continue
      }
      if (t < ts) break
      const prev = pos
      if (t >= te) pos = s.pointer
      else {
        const p = ease(seg(t, ts, te))
        pos = { x: lerp(prev.x, s.pointer.x, p), y: lerp(prev.y, s.pointer.y, p) }
        break
      }
    }
    return pos
  }

  return {
    root,
    update(t, pending) {
      const cam = camAt(t)
      const S = S0 * cam.z
      let left = 960 - cam.cx * S
      let top = 540 - cam.cy * S
      left = 1440 * S <= 1920 ? (1920 - 1440 * S) / 2 : clamp(left, 1920 - 1440 * S, 0)
      top = 900 * S <= 1080 ? (1080 - 900 * S) / 2 : clamp(top, 1080 - 900 * S, 0)
      win.style.transform = `translate(${left}px, ${top}px) scale(${S})`
      win.style.borderRadius = `${16 / S}px`

      const k = shotIndex(shots, t)
      imgs.forEach((im, i) => {
        const s = shots[i]
        const fade = i === 0 ? 1 : seg(t, s.t0, s.t0 + 0.16)
        let show = false
        let op = 1
        if (i === k) {
          show = true
          op = fade
        } else if (i === k - 1 && seg(t, shots[k].t0, shots[k].t0 + 0.16) < 1) show = true
        else if (i === k + 1 && shots[k + 1].t0 - t < 1.2) {
          // the next picture is made ready (decoded) a moment before it is needed, but not shown
          im.style.display = 'block'
          im.style.opacity = '0'
          pending.push(decoded(im))
          return
        }
        im.style.display = show ? 'block' : 'none'
        im.style.opacity = String(op)
        if (show) pending.push(decoded(im))
      })

      // pointer and clicks
      const hasPointer = shots.some((s) => s.pointer)
      if (hasPointer) {
        const p = pointerAt(t)
        cur.c.style.display = 'block'
        cur.c.style.left = px(p.x)
        cur.c.style.top = px(p.y)
        cur.c.style.transform = `scale(${1 / S})`
        cur.c.style.opacity = String(Math.min(seg(t, def.t0, def.t0 + 0.5), 1 - seg(t, def.t1 - 0.2, def.t1 + 0.2)))
      }
      let ring = null
      for (let i = 0; i < shots.length; i++) {
        const s = shots[i]
        if (!s.pointer || s.click === false || i === 0 && !s.clickFirst) continue
        const tc = s.t0 - 0.1
        if (t >= tc && t <= tc + 0.55) ring = { x: s.pointer.x, y: s.pointer.y, p: (t - tc) / 0.55 }
      }
      if (ring) {
        cur.rip.style.display = 'block'
        cur.rip.style.left = px(ring.x)
        cur.rip.style.top = px(ring.y)
        cur.rip.style.opacity = String(1 - ring.p)
        cur.rip.style.transform = `scale(${(0.35 + easeOut(ring.p) * 1.35) / S})`
      } else cur.rip.style.display = 'none'

      overlays.forEach((o) => o.update(t))
      if (chips) chips.update(t)
    },
  }
}

// ------------------------------------------------------------------------------------------------------ title scenes
function kineticScene(def) {
  const root = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
  const items = def.items.map((it) => {
    const d = el('div', { position: 'absolute', left: px(it.x ?? 200), top: px(it.y), fontSize: px(it.size ?? 130), fontWeight: it.weight ?? 800, color: it.color ?? COL.paper, letterSpacing: '-3px', lineHeight: '1.05', whiteSpace: 'nowrap', opacity: '0' }, root, it.text)
    return d
  })
  return {
    root,
    update(t) {
      items.forEach((d, i) => {
        const it = def.items[i]
        const a = seg(t, it.t, it.t + 0.55)
        const out = seg(t, it.out ?? def.t1 - 0.3, (it.out ?? def.t1 - 0.3) + 0.35)
        d.style.opacity = String(a * (1 - out))
        d.style.transform = `translateY(${(1 - easeOut(a)) * 48}px)`
      })
    },
  }
}

function promiseScene(def) {
  const root = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
  const lg = logo(root, 96, 160, 150)
  const brand = el('div', { position: 'absolute', left: '280px', top: '158px', fontSize: '60px', fontWeight: 800, letterSpacing: '-1px' }, root, 'Reel Studio')
  const h1 = el('div', { position: 'absolute', left: '160px', top: '290px', width: '960px', fontSize: '128px', fontWeight: 800, lineHeight: '1.03', letterSpacing: '-4px' }, root)
  h1.innerHTML = `Script in.<br><span style="color:${COL.orange}">Finished</span><br>video out.`
  const sub = el('div', { position: 'absolute', left: '160px', top: '770px', width: '900px', fontSize: '44px', fontWeight: 500, lineHeight: '1.35', color: COL.muted }, root, 'Offline-first production of 45–60 second vertical videos.')
  const phones = def.phones.map((p, i) => {
    const d = el('div', { position: 'absolute', left: px(1150 + i * 230), top: px(260 + (i === 1 ? -50 : 20)), width: '210px', height: '374px', borderRadius: '30px', overflow: 'hidden', boxShadow: '0 30px 70px rgba(0,0,0,0.55)', border: '2px solid rgba(255,255,255,0.18)', background: '#000', opacity: '0' }, root)
    const im = loadImg(p)
    Object.assign(im.style, { width: '210px', height: '374px', objectFit: 'cover' })
    d.appendChild(im)
    d.__im = im
    return d
  })
  return {
    root,
    update(t, pending) {
      const l = t - def.t0
      lg.style.opacity = String(seg(l, 0.1, 0.6))
      lg.style.transform = `scale(${0.6 + 0.4 * easeOutBack(seg(l, 0.1, 0.7))})`
      brand.style.opacity = String(seg(l, 0.3, 0.8))
      h1.style.opacity = String(seg(l, 0.6, 1.2))
      h1.style.transform = `translateY(${(1 - easeOut(seg(l, 0.6, 1.3))) * 50}px)`
      sub.style.opacity = String(seg(l, 1.6, 2.2))
      phones.forEach((d, i) => {
        const a = seg(l, 1.0 + i * 0.35, 1.9 + i * 0.35)
        d.style.opacity = String(a)
        const dy = (1 - easeOutBack(a, 0.8)) * 160
        d.style.transform = `translateY(${dy}px) rotate(${(i - 1) * 4 * (1 - a * 0.5)}deg)`
        pending.push(decoded(d.__im))
      })
    },
  }
}

function phoneScene(def) {
  const root = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
  const body = el('div', { position: 'absolute', left: '250px', top: '150px', width: '454px', height: '800px', borderRadius: '70px', background: '#0b0a14', border: '3px solid rgba(255,255,255,0.22)', boxShadow: '0 50px 120px rgba(0,0,0,0.65), 0 0 0 10px rgba(107,92,231,0.18)', opacity: '0' }, root)
  const cv = el('canvas', { position: 'absolute', left: '22px', top: '38px', width: '410px', height: '729px', borderRadius: '44px', background: '#000' }, body)
  cv.width = 540
  cv.height = 960
  const g = cv.getContext('2d')
  const frames = def.frames.map(loadImg)
  const notch = el('div', { position: 'absolute', left: '167px', top: '14px', width: '120px', height: '14px', borderRadius: '8px', background: '#1b1830' }, body)
  const rows = def.rows.map((r) => {
    const d = el('div', { position: 'absolute', left: '860px', top: px(r.y), width: '900px', opacity: '0' }, root)
    d.innerHTML = `<div style="font-size:${r.size ?? 150}px;font-weight:800;line-height:1.05;letter-spacing:-5px;color:${r.color ?? COL.paper}">${r.big}</div><div style="margin-top:10px;font-size:38px;font-weight:600;line-height:1.3;color:${COL.muted}">${r.label}</div>`
    return d
  })
  return {
    root,
    update(t, pending) {
      const l = t - def.t0
      body.style.opacity = String(seg(l, 0.1, 0.7))
      body.style.transform = `translateY(${(1 - easeOut(seg(l, 0.1, 0.9))) * 80}px) rotate(${(1 - easeOut(seg(l, 0.1, 1.2))) * -3}deg)`
      // the real render, played from its own frames
      const f = clamp(Math.floor((t - def.t0 - def.playFrom) * def.fps), 0, frames.length - 1)
      const im = frames[f]
      if (im && im.complete && im.naturalWidth) g.drawImage(im, 0, 0, 540, 960)
      else if (im) pending.push(decoded(im).then(() => im.naturalWidth && g.drawImage(im, 0, 0, 540, 960)))
      def.rows.forEach((r, i) => {
        const a = seg(t, r.t, r.t + 0.5)
        rows[i].style.opacity = String(a)
        rows[i].style.transform = `translateX(${(1 - easeOut(a)) * 70}px)`
      })
    },
  }
}

function outroScene(def) {
  const root = el('div', { position: 'absolute', left: '0px', top: '0px', width: '1920px', height: '1080px' })
  const lg = logo(root, 150, 885, 170)
  const name = el('div', { position: 'absolute', left: '0px', top: '360px', width: '1920px', textAlign: 'center', fontSize: '118px', fontWeight: 800, letterSpacing: '-3px' }, root, 'Reel Studio')
  const tag = el('div', { position: 'absolute', left: '0px', top: '520px', width: '1920px', textAlign: 'center', fontSize: '66px', fontWeight: 700, color: COL.violetL, letterSpacing: '-1px' }, root, 'Script in. Video out.')
  const chips = def.chips.map((c) => {
    const d = el('div', { position: 'absolute', top: '700px', padding: '22px 52px', borderRadius: '70px', fontSize: '48px', fontWeight: 800, background: c.bg, color: c.color ?? COL.paper, whiteSpace: 'nowrap', opacity: '0', boxShadow: '0 20px 50px rgba(0,0,0,0.4)' }, root, c.text)
    d.__c = c
    return d
  })
  const foot = el('div', { position: 'absolute', left: '0px', top: '930px', width: '1920px', textAlign: 'center', fontSize: '34px', fontWeight: 600, letterSpacing: '4px', textTransform: 'uppercase', color: 'rgba(246,242,234,0.6)' }, root, 'Offline-first · Repeatable · Yours')
  // chips are centred as a row once their widths are known
  const place = () => {
    const gap = 36
    const w = chips.map((d) => d.offsetWidth)
    const total = w.reduce((a, b) => a + b, 0) + gap * (chips.length - 1)
    let x = (1920 - total) / 2
    chips.forEach((d, i) => {
      d.style.left = px(x)
      x += w[i] + gap
    })
  }
  return {
    root,
    update(t) {
      const l = t - def.t0
      if (!root.__placed) {
        root.style.display = 'block'
        place()
        root.__placed = true
      }
      lg.style.opacity = String(seg(l, 0.1, 0.6))
      lg.style.transform = `scale(${0.5 + 0.5 * easeOutBack(seg(l, 0.1, 0.8), 1.6)})`
      name.style.opacity = String(seg(l, 0.4, 1.0))
      tag.style.opacity = String(seg(l, 0.9, 1.5))
      tag.style.transform = `translateY(${(1 - easeOut(seg(l, 0.9, 1.6))) * 30}px)`
      chips.forEach((d) => {
        const a = seg(t, d.__c.t, d.__c.t + 0.5)
        d.style.opacity = String(a)
        d.style.transform = `translateY(${(1 - easeOutBack(a, 1.1)) * 50}px) scale(${0.85 + 0.15 * a})`
      })
      foot.style.opacity = String(seg(l, 3.2, 4.0))
    },
  }
}

// ------------------------------------------------------------------------------------------------------ the film
const makers = { kinetic: kineticScene, promise: promiseScene, ui: uiScene, phone: phoneScene, outro: outroScene }
const scenes = TL.scenes.map((def) => ({ def, ...makers[def.kind](def) }))

window.seek = async (t) => {
  updateBg(t)
  const pending = []
  for (const s of scenes) {
    const { t0, t1 } = s.def
    const visible = t >= t0 - 0.001 && t < t1 + 0.36
    s.root.style.display = visible ? 'block' : 'none'
    if (!visible) continue
    s.root.style.opacity = String(s.def.noFadeIn ? 1 : seg(t, t0, t0 + 0.35))
    s.update(t, pending)
  }
  await Promise.all(pending)
  return true
}
window.film = { duration: TL.duration, fps: TL.fps }
window.ready = (async () => {
  if (document.fonts && document.fonts.ready) await document.fonts.ready
  await window.seek(0)
  return true
})()
