import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { HighlightStyle, bracketMatching, syntaxHighlighting } from '@codemirror/language'
import { linter, lintGutter } from '@codemirror/lint'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { tags as t } from '@lezer/highlight'
import { Check, Copy, Download } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReelSpec } from '@/api/types'
import { Button, Chip } from '@/components/ui'
import { cn } from '@/lib/cn'
import { normalizeSpec, stripDefaults } from '@/lib/normalize'
import { useProject } from '@/store/project'

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '12px', backgroundColor: 'var(--panel)', color: 'var(--text)' },
  '.cm-scroller': { fontFamily: 'var(--font-mono)', lineHeight: '1.55' },
  '.cm-content': { caretColor: 'var(--accent)', padding: '8px 0' },
  '.cm-gutters': { backgroundColor: 'var(--panel)', color: 'var(--faint)', border: 'none' },
  '.cm-activeLine': { backgroundColor: 'var(--hover)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--hover)' },
  '.cm-cursor': { borderLeftColor: 'var(--accent)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'var(--accent-soft) !important' },
  '.cm-diagnostic': { fontFamily: 'var(--font-sans)' },
})

const highlight = HighlightStyle.define([
  { tag: t.propertyName, color: 'var(--info)' },
  { tag: t.string, color: 'var(--success)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--warning)' },
  { tag: t.punctuation, color: 'var(--muted)' },
])

const pretty = (spec: ReelSpec): string => JSON.stringify(stripDefaults(spec), null, 2)

/** Line-level diff (LCS) between the saved and the current text, for a quick "what changed" view. */
export function lineDiff(a: string, b: string): { type: ' ' | '+' | '-'; text: string }[] {
  const x = a.split('\n')
  const y = b.split('\n')
  if (x.length * y.length > 4_000_000) return [{ type: ' ', text: '(too large to compare line by line)' }]
  const dp: number[][] = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0))
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--) dp[i][j] = x[i] === y[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: { type: ' ' | '+' | '-'; text: string }[] = []
  let i = 0
  let j = 0
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) (out.push({ type: ' ', text: x[i] }), i++, j++)
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ type: '-', text: x[i++] })
    else out.push({ type: '+', text: y[j++] })
  }
  while (i < x.length) out.push({ type: '-', text: x[i++] })
  while (j < y.length) out.push({ type: '+', text: y[j++] })
  return out
}

export function JsonPanel() {
  const spec = useProject((s) => s.spec) as ReelSpec
  const savedSpec = useProject((s) => s.savedSpec)
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const typing = useRef(false)
  const gestureTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [showDiff, setShowDiff] = useState(false)
  const [copied, setCopied] = useState(false)
  const text = useMemo(() => pretty(spec), [spec])

  // create the editor once
  useEffect(() => {
    if (!host.current) return
    const apply = EditorView.updateListener.of((u) => {
      if (!u.docChanged || !typing.current) return
      const src = u.state.doc.toString()
      try {
        const parsed = JSON.parse(src)
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('A spec is a JSON object')
        setError(null)
        const st = useProject.getState()
        if (!st.gesture) st.beginGesture()
        clearTimeout(gestureTimer.current)
        gestureTimer.current = setTimeout(() => useProject.getState().endGesture(), 1500)
        st.edit((d) => {
          // replace the whole document: keys the user removed must go too
          for (const k of Object.keys(d)) delete (d as unknown as Record<string, unknown>)[k]
          Object.assign(d, normalizeSpec(parsed))
        }, { live: true })
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: text,
        extensions: [lineNumbers(), history(), keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]), bracketMatching(), json(), linter(jsonParseLinter()), lintGutter(), syntaxHighlighting(highlight), theme, EditorView.contentAttributes.of({ 'aria-label': 'Spec JSON', spellcheck: 'false', tabindex: '0' }), apply, EditorView.domEventHandlers({ focus: () => ((typing.current = true), false), blur: () => ((typing.current = false), false) })],
      }),
    })
    view.current = v
    return () => v.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // visual edits flow into the editor (unless the user is typing in it)
  useEffect(() => {
    const v = view.current
    if (!v || typing.current) return
    if (v.state.doc.toString() !== text) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } })
      setError(null)
    }
  }, [text])

  const diff = useMemo(() => (showDiff && savedSpec ? lineDiff(pretty(savedSpec), text) : null), [showDiff, savedSpec, text])
  const changed = useMemo(() => (savedSpec ? lineDiff(pretty(savedSpec), text).filter((l) => l.type !== ' ').length : 0), [savedSpec, text])

  const copy = async () => {
    await navigator.clipboard?.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  const download = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([text + '\n'], { type: 'application/json' }))
    a.download = `${spec.meta.title.toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'reel'}.reel.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 3000)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-3 py-2">
        <Chip tone={error ? 'danger' : 'success'}>{error ? 'Invalid JSON' : 'Valid'}</Chip>
        <Button size="sm" variant="ghost" onClick={() => setShowDiff(!showDiff)} aria-pressed={showDiff}>
          {changed ? `${changed} lines changed` : 'No changes'}
        </Button>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={() => void copy()}>
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} {copied ? 'Copied' : 'Copy'}
        </Button>
        <Button size="sm" variant="ghost" onClick={download}>
          <Download className="size-3.5" /> Save as…
        </Button>
      </div>
      {error && (
        <p className="shrink-0 border-b border-line bg-danger/10 px-3 py-1.5 text-[12px] text-danger" role="alert">
          {error}. The last valid version stays applied until this parses.
        </p>
      )}
      <div ref={host} className={cn('min-h-0 flex-1 overflow-hidden', diff && 'hidden')} />
      {diff && (
        <pre className="min-h-0 flex-1 overflow-auto p-2 font-mono text-[11.5px] leading-[1.5]">
          {diff.map((l, i) => (
            <div key={i} className={cn('whitespace-pre px-2', l.type === '+' && 'bg-success/12 text-success', l.type === '-' && 'bg-danger/12 text-danger')}>
              {l.type} {l.text}
            </div>
          ))}
        </pre>
      )}
    </div>
  )
}
