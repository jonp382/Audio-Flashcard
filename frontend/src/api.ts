// Typed wrapper around window.pywebview.api (agent.md §10).
// Waits for `pywebviewready`, unwraps the {ok, data | error} envelope, and throws a
// typed BridgeError on failure. Components switch on `code`, never on message text.

export type ErrorCode =
  | 'not_found'
  | 'invalid_input'
  | 'busy'
  | 'whisper_not_ready'
  | 'no_speech'
  | 'llm_unavailable'
  | 'grading_failed'
  | 'mic_error'
  | 'import_error'
  | 'internal_error'

export class BridgeError extends Error {
  code: ErrorCode
  constructor(code: ErrorCode, message: string) {
    super(message)
    this.code = code
    this.name = 'BridgeError'
  }
}

export type Language = 'ja' | 'en'
export type CardStateName = 'new' | 'learning' | 'review' | 'relearning'

export interface Counts {
  new: number
  learning: number
  review: number
  practice?: number // cards left in today's practice pass (custom study)
}

export interface CustomStudyOptions {
  new_available: number // unseen new cards beyond today's current limit
  studied_today: number // cards studied today that are not due again yet
  learning_today: number // cards studied today still in learning, due again soon (normal Study)
  extra_new_today: number
}

export interface Deck {
  id: number
  name: string
  language: Language
  new_per_day: number
  reviews_per_day: number
  learning_steps: number[]
  relearning_steps: number[]
  created_at: string
  counts?: Counts
  total_cards?: number
  // learning cards due after the learn-ahead window (list_decks only)
  learning_later?: { count: number; next_due_at: string | null }
}

export interface Card {
  id: number
  deck_id: number
  front: string
  back: string
  reading: string | null
  alt_answers: string[]
  sentence: string | null // example sentence; the card's word may be wrapped in <b>…</b>
  sentence_meaning: string | null // its translation (shown only after answering)
  notes: string | null // deck author's notes; grader context, shown after answering
  state: CardStateName
  learning_step: number | null
  ease_factor: number
  interval_days: number
  repetitions: number
  due_date: string | null
  due_at: string | null
  last_review: string | null
  created_at: string
  updated_at: string
  practice?: boolean // shown as part of a practice pass (only from get_next_card)
  // Two-step card: say the reading, then the meaning (from get_next_card / get_card)
  meaning_step?: boolean
}

export interface NextCard {
  card: Card | null
  counts: Counts
  next_learning_due_at: string | null
}

export type WhisperState = 'missing' | 'downloading' | 'loading' | 'ready' | 'error'

export interface Status {
  whisper_state: WhisperState
  whisper_device: 'cpu' | 'cuda'
  whisper_error: string | null
  ollama_checked: boolean
  ollama_reachable: boolean
  llm_model: string
  llm_model_present: boolean
  mic_open: boolean
  mic_device: string | null
  mic_fallback_used: boolean
  setup_steps: string[]
}

export interface DownloadStatus {
  state: 'idle' | 'downloading' | 'done' | 'error'
  bytes_done: number
  bytes_total: number
  error: string | null
}

export interface Settings {
  ollama_host: string
  ollama_port: number
  llm_model: string
  whisper_device: 'cpu' | 'cuda'
  mic_device: string | null
  record_mode: 'toggle' | 'hold'
  mic_gain: number // linear, 1-16
}

export interface InputDevice {
  name: string
  is_default: boolean
}

export interface RecordingStatus {
  mic_open: boolean
  recording: boolean
  elapsed_s: number
  level: number // 0-1 on a dB scale (-60 dBFS .. 0 dBFS)
  peak: number
  clipping: boolean
  capped: boolean
}

export interface MicTestResult {
  seconds: number
  level: number
  peak: number
  gate_level: number // clips quieter than this are treated as silence
  too_quiet: boolean
  clipping: boolean
  transcript: string | null
  transcript_note: string | null
}

export type AnswerStep = 'reading' | 'meaning'

export interface StepResult {
  step: AnswerStep
  transcript: string
  score: 0 | 1 | 2 | 3
  score_original: number | null
  overridden: boolean
  feedback: string
}

/** stop_recording after the reading step of a two-step card: transcribed, not graded. */
export interface HeardResult {
  stage: 'heard'
  step: 'reading'
  card_id: number
  transcript: string
}

export interface ReviewResult {
  review_id: number
  card_id: number
  deck_id: number
  transcript: string
  score: 0 | 1 | 2 | 3
  score_original: number | null
  overridden: boolean
  is_correct: boolean
  feedback: string
  expected: { front: string; back: string; reading: string | null; alt_answers: string[] }
  state: CardStateName
  next_due: string | null
  interval_days: number
  ease_factor: number
  recording_id: number | null
  practice: boolean
  steps: StepResult[] | null // two-step cards; score is then the lowest step score
}

export interface ReviewHistoryRow {
  id: number
  reviewed_at: string
  transcript: string
  score: number
  score_original: number | null
  overridden: boolean
  is_correct: boolean
  feedback: string
  state_before: CardStateName
  state_after: CardStateName
  interval_after: number
  ease_after: number
  recording_id: number | null
  steps: StepResult[] | null
}

export interface WeakSpotCard {
  card_id: number
  front: string
  reading: string | null
  back: string
  answers: number // two-step reviews of this card in the window
  reading_misses: number
  meaning_misses: number
  last_missed_at: string | null
  recovered: boolean // the latest answer was right on every step
}

export interface WeakSpots {
  days: number // 0 = all history
  steps: Record<AnswerStep, { answers: number; correct: number }>
  cards: WeakSpotCard[]
}

export interface ImportPreview {
  columns: string[]
  rows: string[][]
  total_rows: number
  detected: { encoding: string; delimiter: string; anki_export: boolean }
}

export interface ImportMapping {
  front: number
  back: number
  reading: number | null
  sentence: number | null
  sentence_meaning: number | null
  notes: number | null
}

export interface MissingCard {
  card_id: number
  front: string
  reading: string | null
  back: string
}

/** What an import would do (import_plan), without changing anything. */
export interface ImportPlan {
  new: number
  updated: number // cards already in the deck whose text changes (progress kept)
  unchanged: number
  skipped_duplicates: number
  errors: { row: number; reason: string }[]
  missing: MissingCard[] // deck cards no longer in the file
}

export interface ImportResult extends ImportPlan {
  imported: number // new cards added
  removed: number
}

type Envelope<T> = { ok: true; data: T } | { ok: false; error: { code: ErrorCode; message: string } }

// The Python side's method table; every method returns an Envelope.
export interface PyApi {
  [method: string]: (...args: unknown[]) => Promise<Envelope<unknown>>
}

declare global {
  interface Window {
    pywebview?: { api: PyApi }
  }
}

let ready: Promise<PyApi> | null = null

function bridge(): Promise<PyApi> {
  if (ready) return ready
  ready = new Promise<PyApi>((resolve) => {
    const done = () => resolve(window.pywebview!.api)
    if (window.pywebview?.api) return done()
    window.addEventListener('pywebviewready', done, { once: true })
    if (import.meta.env.DEV) {
      // Plain browser on the Vite dev server: fall back to an in-memory mock.
      setTimeout(async () => {
        if (!window.pywebview?.api) {
          const { createMockApi } = await import('./mock')
          console.info('[api] pywebview not found; using in-memory mock')
          resolve(createMockApi())
        }
      }, 400)
    }
  })
  return ready
}

async function call<T>(method: string, ...args: unknown[]): Promise<T> {
  const api = await bridge()
  const fn = api[method]
  if (!fn) throw new BridgeError('internal_error', `Bridge method ${method} missing`)
  const res = (await fn(...args)) as Envelope<T>
  if (res && res.ok) return res.data
  if (res && !res.ok) throw new BridgeError(res.error.code, res.error.message)
  throw new BridgeError('internal_error', `Malformed response from ${method}`)
}

export const api = {
  // status & setup
  getStatus: () => call<Status>('get_status'),
  downloadWhisperModel: () => call<DownloadStatus>('download_whisper_model'),
  getDownloadStatus: () => call<DownloadStatus>('get_download_status'),
  openOllamaDownloadPage: () => call<null>('open_ollama_download_page'),
  recheckOllama: () => call<Status>('recheck_ollama'),
  // settings & devices
  getSettings: () => call<Settings>('get_settings'),
  updateSettings: (patch: Partial<Settings>) => call<Settings>('update_settings', patch),
  listInputDevices: () => call<InputDevice[]>('list_input_devices'),
  // decks & cards
  listDecks: () => call<Deck[]>('list_decks'),
  getDeck: (deckId: number) => call<Deck>('get_deck', deckId),
  createDeck: (name: string, language: Language) => call<Deck>('create_deck', name, language),
  updateDeck: (
    deckId: number,
    patch: Partial<Pick<Deck, 'name' | 'language' | 'new_per_day' | 'reviews_per_day'>> & {
      learning_steps?: number[] | string
      relearning_steps?: number[] | string
    },
  ) => call<Deck>('update_deck', deckId, patch),
  deleteDeck: (deckId: number) => call<null>('delete_deck', deckId),
  listCards: (deckId: number) => call<Card[]>('list_cards', deckId),
  getCard: (cardId: number) => call<Card>('get_card', cardId),
  createCard: (
    deckId: number,
    front: string,
    back: string,
    reading: string | null,
    altAnswers: string[],
    sentence: string | null = null,
    sentenceMeaning: string | null = null,
    notes: string | null = null,
  ) => call<Card>('create_card', deckId, front, back, reading, altAnswers, sentence, sentenceMeaning, notes),
  updateCard: (
    cardId: number,
    patch: Partial<Pick<Card, 'front' | 'back' | 'reading' | 'alt_answers' | 'sentence' | 'sentence_meaning' | 'notes'>>,
  ) =>
    call<Card>('update_card', cardId, patch),
  deleteCard: (cardId: number) => call<null>('delete_card', cardId),
  getCustomStudyOptions: (deckId: number) => call<CustomStudyOptions>('get_custom_study_options', deckId),
  startCustomStudy: (deckId: number, extraNew: number, reviewStudied: boolean) =>
    call<NextCard>('start_custom_study', deckId, extraNew, reviewStudied),
  getNextCard: (deckId: number) => call<NextCard>('get_next_card', deckId),
  // import
  pickImportFile: () => call<{ path: string } | null>('pick_import_file'),
  importPreview: (path: string) => call<ImportPreview>('import_preview', path),
  importCards: (path: string, deckId: number, mapping: ImportMapping | null) =>
    call<ImportResult>('import_cards', path, deckId, mapping),
  exportDeck: (deckId: number) => call<{ path: string; cards: number } | null>('export_deck', deckId),
  importDeckFile: () => call<{ deck: Deck; cards: number; reviews: number } | null>('import_deck_file'),
  importPlan: (path: string, deckId: number, mapping: ImportMapping | null) =>
    call<ImportPlan>('import_plan', path, deckId, mapping),
  importCardsMissing: (path: string, deckId: number, mapping: ImportMapping | null, missing: 'keep' | 'remove') =>
    call<ImportResult>('import_cards', path, deckId, mapping, missing),
  // session & recording
  beginSession: (deckId: number) => call<NextCard>('begin_session', deckId),
  endSession: () => call<null>('end_session'),
  openMic: () => call<null>('open_mic'),
  closeMic: () => call<null>('close_mic'),
  startRecording: (cardId: number, step?: AnswerStep) =>
    step ? call<null>('start_recording', cardId, step) : call<null>('start_recording', cardId),
  getRecordingStatus: () => call<RecordingStatus>('get_recording_status'),
  cancelRecording: () => call<null>('cancel_recording'),
  stopRecording: () => call<ReviewResult | HeardResult>('stop_recording'),
  retryGrading: () => call<ReviewResult>('retry_grading'),
  overrideGrade: (reviewId: number, correct: boolean, step?: AnswerStep) =>
    step ? call<ReviewResult>('override_grade', reviewId, correct, step) : call<ReviewResult>('override_grade', reviewId, correct),
  undoLastReview: () => call<{ card_id: number; deck_id: number }>('undo_last_review'),
  // mic test
  micTestStart: () => call<null>('mic_test_start'),
  micTestStop: (language: Language) => call<MicTestResult>('mic_test_stop', language),
  micTestCancel: () => call<null>('mic_test_cancel'),
  micTestPlay: () => call<null>('mic_test_play'),
  // history & playback
  listReviews: (cardId: number) => call<ReviewHistoryRow[]>('list_reviews', cardId),
  getWeakSpots: (deckId: number, days: number) => call<WeakSpots>('get_weak_spots', deckId, days),
  listRecordings: (cardId: number) => call<{ id: number; created_at: string }[]>('list_recordings', cardId),
  playRecording: (recordingId: number) => call<null>('play_recording', recordingId),
  stopPlayback: () => call<null>('stop_playback'),
}

export function isBridgeError(e: unknown, ...codes: ErrorCode[]): e is BridgeError {
  return e instanceof BridgeError && (codes.length === 0 || codes.includes(e.code))
}

export function errorMessage(e: unknown): string {
  if (e instanceof BridgeError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}
