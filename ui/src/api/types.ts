// The shapes the engine speaks (mirrors reel's spec models, lint report and the server's JSON).
// Times inside a scene are scene-local seconds; positions are screen fractions.

export type Seconds = number
export type Vec2 = [number, number]

export type PaletteRole = 'skin' | 'hair' | 'shirt' | 'shirt2' | 'pants' | 'shoes' | 'accent' | 'eye' | 'outline'

export interface FxSpec {
  grain?: number | null
  vignette?: number | null
  bloom?: number | null
  chromatic_aberration?: number | null
  saturation?: number | null
  contrast?: number | null
  warmth?: number | null
  letterbox?: number | null
}

export interface SafeArea {
  top: number
  bottom: number
  left: number
  right: number
}

export interface Meta {
  title: string
  style: string
  fps: number
  resolution: [number, number]
  seed: number
  target_duration_sec: number
  aspect?: '9:16'
  fx?: FxSpec | null
  safe_area?: SafeArea | null
}

export interface Character {
  id: string
  archetype: string
  name?: string | null
  voice?: string | null
  props: string[]
  palette: Partial<Record<PaletteRole, string>>
}

export interface CameraMove {
  type: string
  from?: number | string | Vec2 | null
  to?: number | string | Vec2 | null
  t0: Seconds
  t1: Seconds
  ease: string
  params: Record<string, unknown>
}

export interface ActionClip {
  name: string
  t0: Seconds
  t1: Seconds
  params: Record<string, unknown>
}

export type Depth = 'background' | 'mid' | 'foreground'

export interface Layer {
  character: string
  position: Vec2 | string
  scale: number
  depth: Depth
  facing: 'auto' | 'left' | 'right'
  actions: ActionClip[]
}

export type CaptionStyle = 'subtitle' | 'title' | 'shout' | string

export interface Caption {
  text: string
  t0: Seconds
  t1: Seconds
  style: CaptionStyle
  speaker?: string | null
  speak?: boolean | null
  anchor: 'auto' | 'top' | 'center' | 'bottom'
}

export interface SfxEvent {
  name: string
  t: Seconds
  volume: number
}

export interface Transition {
  type: string
  duration: number
  params: Record<string, unknown>
}

export interface Scene {
  id: string
  duration_sec: number
  background: { template: string; params: Record<string, unknown> }
  camera: { moves: CameraMove[] }
  layers: Layer[]
  captions: Caption[]
  sfx: SfxEvent[]
  transition_out: Transition
  notes?: string | null
}

export interface AudioSpec {
  music: string | null
  voiceover: 'tts' | 'file' | 'none'
  ducking: boolean
  voiceover_file?: string | null
  tts_voice?: string | null
  music_gain_db: number
  voice_gain_db: number
  sfx_gain_db: number
  auto_sfx: boolean
}

export interface ReelSpec {
  version: string
  meta: Meta
  characters: Character[]
  scenes: Scene[]
  audio: AudioSpec
}

// ---------------------------------------------------------------- the catalog
export interface Param {
  name: string
  type: string // 'string' | 'number' | 'integer' | 'boolean' | 'array | string | null' ...
  required: boolean
  default?: unknown
  enum?: string[]
  minimum?: number
  maximum?: number
  description?: string
}

export interface CatalogEntry {
  name: string
  summary: string
  params?: Param[]
  category?: string
  moves_root?: boolean
  min_duration?: number
  default_duration?: number
  tags?: string[]
  slots?: Record<string, Vec2>
  ground_y?: number
  perspective?: number
  palette?: Partial<Record<PaletteRole, string>>
  /** archetypes: design units tall; on screen a character is about height * 1.12 / 1920 of the frame at scale 1 */
  height?: number
}

export interface Catalog {
  version: number
  styles: CatalogEntry[]
  backgrounds: CatalogEntry[]
  actions: CatalogEntry[]
  transitions: CatalogEntry[]
  camera_moves: CatalogEntry[]
  caption_styles: CatalogEntry[]
  archetypes: CatalogEntry[]
  props: CatalogEntry[]
  sfx: CatalogEntry[]
  easings: CatalogEntry[]
  music_moods: string[]
  palette_roles: PaletteRole[]
  universal_slots: string[]
  limits: { min_total_sec: number; max_total_sec: number; max_scene_sec: number }
}

// ---------------------------------------------------------------- lint
export type Severity = 'error' | 'warning' | 'info'

export interface LintIssue {
  severity: Severity
  code: string
  path: string
  message: string
  hint?: string
  kind?: string
  name?: string
}

export interface LintReport {
  ok: boolean
  source?: string | null
  total_sec: number | null
  n_scenes: number | null
  counts: { errors: number; warnings: number; infos: number }
  missing: Record<string, Record<string, string[]>>
  issues: LintIssue[]
}

// ---------------------------------------------------------------- projects, previews, jobs
export interface ProjectSummary {
  id: string
  title: string
  style?: string
  scenes?: number
  duration_sec?: number
  etag?: string
  updated_at: number
  lint?: { errors: number; warnings: number }
  thumb_url?: string
  broken?: string
}

export interface ProjectDoc {
  id: string
  spec: ReelSpec
  script: string
  etag: string
  updated_at: number
  thumb_url?: string
}

export interface PreviewInfo {
  preview_id: string
  fps: number
  total_frames: number
  width: number
  height: number
}

export interface ExampleScript {
  id: string
  title: string
  language: string
  words: number
  text: string
}

export interface ExampleSpec {
  id: string
  title: string
  style: string
  scenes: number
  duration_sec: number
}

export interface Examples {
  specs: ExampleSpec[]
  scripts: ExampleScript[]
}

export type JobEvent =
  | { type: 'status'; status: 'queued' | 'running'; position?: number; seq: number }
  | { type: 'progress'; phase: 'plan' | 'audio' | 'frames' | 'mux'; done: number; total: number; eta_sec?: number | null; seq: number }
  | { type: 'note' | 'warning' | 'log'; message: string; seq: number }
  | { type: 'done'; result: Record<string, unknown>; seq: number }
  | { type: 'error'; message: string; hint?: string; lint?: LintReport | null; spec?: ReelSpec | null; seq: number }
  | { type: 'cancelled'; seq: number }

/** A job event without its sequence number (what a worker emits; the manager numbers it). */
export type JobEventInput = JobEvent extends infer E ? (E extends { seq: number } ? Omit<E, 'seq'> : never) : never

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

export interface JobSnapshot {
  id: string
  kind: string
  title: string
  status: JobStatus
  created_at: number
  started_at: number | null
  finished_at: number | null
  result: Record<string, unknown> | null
  events: JobEvent[]
}

export interface GenerateBody {
  script: string
  style: string
  target_duration: number
  seed: number
  planner: string
  enrich: boolean
  repairs: number
  /** a model's captions are the script's own words, in order (what it rewords, drops or invents is put right) */
  verbatim?: boolean
}

/** How closely a spec's captions follow its script (`POST /api/script/check`). */
export interface ScriptCheck {
  empty: boolean
  /** share of the script's words the captions already show (0..1) */
  covered?: number
  units?: number
  words?: number
  missing?: { text: string; speaker: string | null; kind: string }[]
  /** captions that are only a speaker's name */
  labels?: number
  /** captions whose words are not in the script */
  foreign?: number
  ok?: boolean
  /** the script can be read in a reel (else it has to be condensed, not copied) */
  fits?: boolean
  reading_sec?: number
}

export interface ScriptLockResult {
  spec: ReelSpec
  changed: boolean
  notes: string[]
  lint: LintReport
  coverage: ScriptCheck
}

export interface GenerateResult {
  spec: ReelSpec
  lint: LintReport
  attempts: number
  notes: string[]
  generator: string
}

export type PresetName = 'draft' | 'standard' | 'full' | 'custom'

export interface RenderBody {
  spec?: ReelSpec
  project_id?: string
  preset: PresetName
  scale?: number
  range?: [number, number]
  crf?: number
  maxrate?: string
  workers?: number
  lenient?: boolean
  no_audio?: boolean
  tts?: string
  seed?: number
  style?: string
}

export interface RenderPlan {
  frames: number
  segments_total: number
  segments_cached: number
  est_seconds: number
  width: number
  height: number
  duration_sec: number
  preset: string
  /** 'pending': the voice is not generated yet, so its retiming (and with it most chunks) is not known */
  voice: 'none' | 'ready' | 'pending'
}

export interface RenderResult {
  id: string
  video_url: string
  path: string
  size_bytes: number
  width: number
  height: number
  duration_sec: number
  frames: number
  seconds: number
  bitrate_bps: number
  segments: { total: number; encoded: number; reused: number }
  warnings: string[]
  notes: string[]
  preset: string
  project_id?: string | null
  title?: string
  style?: string
  has_audio: boolean
  created_at: number
}

// ---------------------------------------------------------------- audio
export interface EngineStatus {
  name: 'piper' | 'say' | 'babble' | 'elevenlabs' | string
  label: string
  online: boolean
  available: boolean
  detail: string
  destination?: string
  model?: string
  key_source?: 'shell' | '.env' | null
}

export interface EnginesStatus {
  engines: EngineStatus[]
  default: string
}

export interface VoiceInfo {
  id: string
  name: string
  traits: Record<string, string>
}

export interface SpeechEstimate {
  available: boolean
  reason?: string
  engine: string
  online?: boolean
  lines?: number
  cached_lines?: number
  new_lines?: number
  billable_characters?: number
  destination?: string | null
}

export interface MixReport {
  duration: number
  peak: number
  approx_lufs: number
  ducked_seconds: number
  n_clips: number
}

export interface AudioResult {
  wav_url: string | null
  spec: ReelSpec
  retimed: { scene: number; caption: number; t0: number; t1: number; was_t0: number; was_t1: number }[]
  word_timings: Record<string, Record<string, [number, number, string][]>>
  warnings: string[]
  notes: string[]
  report: MixReport | null
  peaks: { voice: number[]; music: number[]; sfx: number[] }
  /** the gain the mixer puts on the music while somebody speaks (1 = untouched), `per_sec` values a second; null when nothing ducks */
  duck?: { per_sec: number; depth_db: number; gain: number[] } | null
  total_sec: number
}

// ---------------------------------------------------------------- system
export interface Health {
  version: string
  python: string
  ffmpeg: string
  ffmpeg_ok: boolean
  skia: string
  workers: number
  cache_dir: string
  plugins: string[]
  workspace: string
}

export interface DoctorReport {
  reel: string
  python: string
  ffmpeg: { ok: boolean; version: string }
  skia: { ok: boolean; version: string }
  fonts: { role: string; family: string }[]
  tts: EngineStatus[]
  tts_default: string
  llm: {
    ollama: { running: boolean; url: string; models: { name: string; remote: boolean }[] }
    keys: { name: string; source: 'shell' | '.env' | null }[]
    default?: string | null
  }
  counts: Record<string, number>
  cache_dir: string
}

export interface TestResult {
  ok: boolean
  message?: string
  lines?: string[]
  cost?: string
  account?: { tier: string; characters_left: number; characters_limit: number } | null
}

export interface CacheStats {
  root: string
  frames: { files: number; bytes: number }
  segments: { files: number; bytes: number }
  tts: { files: number; bytes: number }
  tmp: { files: number; bytes: number }
  total_bytes: number
}

export class ApiError extends Error {
  status: number
  detail: string
  hint?: string
  extra: Record<string, unknown>
  constructor(status: number, detail: string, hint?: string, extra: Record<string, unknown> = {}) {
    super(detail)
    this.status = status
    this.detail = detail
    this.hint = hint
    this.extra = extra
  }
}
