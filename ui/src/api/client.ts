import type {
  AssetDraft,
  AssetFields,
  AssetInfo,
  AssetKind,
  AssetList,
  AudioResult,
  CacheStats,
  Catalog,
  Coverage,
  DoctorReport,
  DraftLook,
  EnginesStatus,
  Examples,
  FillGapsResult,
  GenerateBody,
  Health,
  JobEvent,
  JobSnapshot,
  LintReport,
  PreviewInfo,
  ProjectDoc,
  ProjectSummary,
  ReelSpec,
  RenderBody,
  RenderPlan,
  RenderResult,
  ScriptCheck,
  ScriptLockResult,
  SpeechEstimate,
  TestResult,
  VoiceInfo,
} from './types'

/** Everything the UI asks of the engine. `HttpAdapter` talks to `reel serve`; `MockAdapter` (VITE_API=mock or ?mock) needs nothing. */
export interface Api {
  readonly kind: 'http' | 'mock'
  health(): Promise<Health>
  catalog(): Promise<Catalog>
  schema(): Promise<unknown>
  examples(): Promise<Examples>

  listProjects(): Promise<ProjectSummary[]>
  getProject(id: string): Promise<ProjectDoc>
  createProject(body: { spec?: ReelSpec; title?: string; script?: string; style?: string; from_example?: string }): Promise<ProjectDoc>
  /** Saves; rejects with ApiError(409, ..., extra.current) when the file changed on disk since `etag`. */
  saveProject(id: string, spec: ReelSpec, script: string | undefined, etag: string): Promise<ProjectDoc>
  deleteProject(id: string): Promise<void>
  projectThumbUrl(p: ProjectSummary): string

  lint(spec: unknown): Promise<LintReport>
  /** How closely the captions follow the script (nothing is changed). */
  scriptCheck(script: string, spec: ReelSpec): Promise<ScriptCheck>
  /** The spec with its captions put back to the script's own words, in order; the caller decides whether to keep it. */
  scriptLock(script: string, spec: ReelSpec): Promise<ScriptLockResult>
  generate(body: GenerateBody): Promise<{ job_id: string }>

  /** `wordTimings` (from the audio job) make the mouths follow the generated voice. */
  createPreview(spec: ReelSpec, scale?: number, wordTimings?: AudioResult['word_timings']): Promise<PreviewInfo>
  frame(previewId: string, n: number, signal?: AbortSignal): Promise<Blob>
  libraryThumbUrl(kind: 'style' | 'background' | 'archetype', name: string, timeOfDay?: string, style?: string): string

  // -- the asset library: characters, objects and places (built in, and the workspace's own)
  listAssets(): Promise<AssetList>
  /** Start adding a file (SVG, PNG, JPG, WebP): the server reads it and says what it found; nothing is saved yet. */
  createAssetDraft(file: Blob, opts?: { filename?: string; kind?: AssetKind }): Promise<AssetDraft>
  /** An unsaved upload drawn as a scene would show it, with the settings chosen so far. */
  assetDraftThumbUrl(draftId: string, look: DraftLook): string
  /** Keep the upload: rejects with ApiError(409, ..., extra.conflict) when the name is taken (pass `replace`). */
  commitAssetDraft(draftId: string, fields: AssetFields, replace?: boolean): Promise<AssetInfo>
  discardAssetDraft(draftId: string): Promise<void>
  /** Change an asset of the workspace library (its words, summary, size, anchor, facing, kind or name). */
  updateAsset(name: string, changes: Partial<AssetFields>): Promise<AssetInfo>
  deleteAsset(name: string): Promise<void>
  assetThumbUrl(asset: Pick<AssetInfo, 'name' | 'version'>, style?: string, timeOfDay?: string, trueScale?: boolean): string
  assetArtUrl(name: string): string
  /** The characters, places and objects a script mentions: which the library can draw and which it lacks. */
  scriptAssets(script: string): Promise<Coverage>
  /** Make a spec use library assets it was missing when it was planned (`meta.library_gaps`); `only` limits which. */
  fillGaps(spec: ReelSpec, only?: { kind: AssetKind; name: string }[]): Promise<FillGapsResult>

  ttsEngines(): Promise<EnginesStatus>
  ttsVoices(engine: string): Promise<VoiceInfo[]>
  /** An audition clip; an online engine refuses (ApiError 409, extra.confirm_required) unless `confirmBilling`. */
  ttsSample(body: { engine: string; voice?: string | null; text: string; confirmBilling?: boolean }): Promise<Blob>
  estimateSpeech(spec: ReelSpec, engine: string | null): Promise<SpeechEstimate>
  prepareAudio(spec: ReelSpec, engine: string | null): Promise<{ job_id: string }>
  sfxUrl(name: string): string
  musicUrl(mood: string, seconds?: number): string
  audioUrl(wavUrl: string): string

  renderPlan(body: RenderBody): Promise<RenderPlan>
  render(body: RenderBody): Promise<{ job_id: string; render_id: string }>
  listRenders(): Promise<RenderResult[]>
  deleteRender(id: string): Promise<void>

  getJob(id: string, after?: number): Promise<JobSnapshot>
  cancelJob(id: string): Promise<void>
  /** Follows a job's events (resuming from `after`); returns an unsubscribe function. */
  subscribeJob(id: string, onEvent: (e: JobEvent) => void, after?: number): () => void

  doctor(): Promise<DoctorReport>
  doctorTest(kind: 'llm' | 'tts', target: string): Promise<TestResult>
  cache(): Promise<CacheStats>
  clearCache(kind: 'frames' | 'segments' | 'tts' | 'tmp'): Promise<CacheStats>
}
