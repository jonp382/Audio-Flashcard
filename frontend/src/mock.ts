// DEV-ONLY in-memory stand-in for the Python bridge, used when the UI runs in a plain
// browser on the Vite dev server. Imported dynamically behind import.meta.env.DEV, so
// it is excluded from production builds. Scheduling here is deliberately naive.
import type { Card, Deck, PyApi, ReviewResult, Settings } from './api'

export function createMockApi(): PyApi {
  const now = () => new Date().toISOString()
  let ids = 100
  const decks: Deck[] = [
    { id: 1, name: 'JLPT N5 verbs', language: 'ja', new_per_day: 20, reviews_per_day: 200, learning_steps: [1, 10], relearning_steps: [10], created_at: now() },
    { id: 2, name: 'Kanji meanings', language: 'en', new_per_day: 20, reviews_per_day: 200, learning_steps: [1, 10], relearning_steps: [10], created_at: now() },
  ]
  const mk = (deck_id: number, front: string, back: string, reading: string | null): Card => ({
    id: ++ids, deck_id, front, back, reading, alt_answers: [], sentence: null, sentence_meaning: null, notes: null, state: 'new', learning_step: null, ease_factor: 2.5,
    interval_days: 0, repetitions: 0, due_date: null, due_at: null, last_review: null, created_at: now(), updated_at: now(),
    meaning_step: deck_id === 1 && !!reading && /[A-Za-z]/.test(back),
  })
  const cards: Card[] = [
    mk(1, '話す', 'to speak', 'はなす'),
    mk(1, '食べる', 'to eat', 'たべる'),
    mk(1, '飲む', 'to drink', 'のむ'),
    { ...mk(1, '方', 'person (polite form)', 'かた'), sentence: '次の<b>方</b>、どうぞ。', sentence_meaning: 'The next person, please.' },
    mk(2, '山', 'mountain', 'やま'),
  ]
  let settings: Settings = { ollama_host: 'localhost', ollama_port: 11434, llm_model: 'qwen3:8b', whisper_device: 'cpu', mic_device: null, record_mode: 'toggle', mic_gain: 1, theme: 'dark', shuffle_due: false, ui_scale: 1 }
  let mic = false
  let recStart: number | null = null
  let recCard: number | null = null
  let recStep: string | null = null
  let heardReading: string | null = null
  const done = new Set<number>()
  let reviewId = 0
  let dl = { state: 'idle' as 'idle' | 'downloading' | 'done' | 'error', bytes_done: 0, bytes_total: 485_000_000, error: null }

  const counts = (deckId: number) => ({ new: cards.filter((c) => c.deck_id === deckId && !done.has(c.id)).length, learning: 0, review: 0 })
  const next = (deckId: number) => ({ card: cards.find((c) => c.deck_id === deckId && !done.has(c.id)) ?? null, counts: counts(deckId), next_learning_due_at: null })
  const ok = (data: unknown = null) => Promise.resolve({ ok: true as const, data })
  const fail = (code: string, message: string) => Promise.resolve({ ok: false as const, error: { code, message } })

  const impl: Record<string, (...a: any[]) => Promise<any>> = {
    get_status: () => ok({ whisper_state: dl.state === 'downloading' ? 'downloading' : 'ready', whisper_device: 'cpu', whisper_error: null, ollama_checked: true, ollama_reachable: true, llm_model: settings.llm_model, llm_model_present: true, mic_open: mic, mic_device: settings.mic_device, mic_fallback_used: false, setup_steps: [] }),
    download_whisper_model: () => { dl = { ...dl, state: 'downloading' }; return ok(dl) },
    get_download_status: () => { if (dl.state === 'downloading') { dl.bytes_done = Math.min(dl.bytes_total, dl.bytes_done + 40_000_000); if (dl.bytes_done >= dl.bytes_total) dl.state = 'done' } return ok(dl) },
    open_ollama_download_page: () => ok(),
    recheck_ollama: () => impl.get_status(),
    get_settings: () => ok(settings),
    update_settings: (p: Partial<Settings>) => { settings = { ...settings, ...p }; return ok(settings) },
    list_input_devices: () => ok([{ name: 'Default mic', is_default: true }, { name: 'USB Microphone', is_default: false }]),
    list_decks: () => ok(decks.map((d) => ({ ...d, counts: counts(d.id), total_cards: cards.filter((c) => c.deck_id === d.id).length }))),
    get_deck: (id: number) => { const d = decks.find((x) => x.id === id); return d ? ok({ ...d, counts: counts(id) }) : fail('not_found', 'Deck not found') },
    create_deck: (name: string, language: 'ja' | 'en') => { const d: Deck = { id: ++ids, name, language, new_per_day: 20, reviews_per_day: 200, learning_steps: [1, 10], relearning_steps: [10], created_at: now() }; decks.push(d); return ok(d) },
    update_deck: (id: number, patch: any) => { const d = decks.find((x) => x.id === id)!; Object.assign(d, patch); for (const k of ['learning_steps', 'relearning_steps'] as const) if (typeof patch[k] === 'string') d[k] = patch[k].split(/\s+/).filter(Boolean).map((s: string) => parseInt(s) * (s.endsWith('h') ? 60 : s.endsWith('d') ? 1440 : 1)); return ok(d) },
    delete_deck: (id: number) => { decks.splice(decks.findIndex((d) => d.id === id), 1); return ok() },
    list_cards: (id: number) => ok(cards.filter((c) => c.deck_id === id)),
    get_card: (id: number) => ok(cards.find((c) => c.id === id)),
    create_card: (deck_id: number, front: string, back: string, reading: string | null, alt: string[]) => { const c = { ...mk(deck_id, front, back, reading), alt_answers: alt ?? [] }; cards.push(c); return ok(c) },
    update_card: (id: number, patch: Partial<Card>) => { const c = cards.find((x) => x.id === id)!; Object.assign(c, patch, { updated_at: now() }); return ok(c) },
    delete_card: (id: number) => { cards.splice(cards.findIndex((c) => c.id === id), 1); return ok() },
    get_next_card: (id: number) => ok(next(id)),
    get_custom_study_options: () => ok({ new_available: 12, studied_today: done.size, learning_today: 0, extra_new_today: 0 }),
    start_custom_study: (id: number) => { done.clear(); return ok(next(id)) },
    pick_import_file: () => ok({ path: '/home/user/cards.txt' }),
    import_preview: () => ok({ columns: ['Front', 'Back', 'Reading'], rows: [['猫', 'cat', 'ねこ'], ['犬', 'dog', 'いぬ']], total_rows: 2, detected: { encoding: 'utf-8-sig', delimiter: 'tab', anki_export: true } }),
    export_deck: () => ok({ path: '/home/user/Kaishi.afdeck', cards: 4 }),
    import_deck_file: () => ok(null),
    import_plan: () => ok({ new: 1, updated: 1, unchanged: 0, skipped_duplicates: 0, errors: [], missing: [{ card_id: 9, front: '次', reading: 'つぎ', back: 'next' }] }),
    import_cards: (_p: string, _d: number, _m: unknown, missing?: string) => ok({ imported: 1, updated: 1, unchanged: 0, removed: missing === 'remove' ? 1 : 0, skipped_duplicates: 0, errors: [], missing: [{ card_id: 9, front: '次', reading: 'つぎ', back: 'next' }] }),
    begin_session: (id: number) => { mic = true; return ok(next(id)) },
    end_session: () => { mic = false; return ok() },
    open_mic: () => { mic = true; return ok() },
    close_mic: () => { mic = false; return ok() },
    start_recording: (cardId: number, step?: string) => { if (recStart) return fail('busy', 'Already recording'); recStart = Date.now(); recCard = cardId; recStep = step ?? null; mic = true; return ok() },
    get_recording_status: () => { const el = recStart ? Math.min(15, (Date.now() - recStart) / 1000) : 0; return ok({ mic_open: mic, recording: recStart !== null, elapsed_s: el, level: Math.min(1, (recStart ? 0.35 + Math.random() * 0.3 : 0.15 + Math.random() * 0.1) + Math.log10(settings.mic_gain) / 3), peak: 0.5, clipping: false, capped: el >= 15 }) },
    mic_test_start: () => { recStart = Date.now(); recCard = null; return ok() },
    mic_test_stop: () => { recStart = null; return ok({ seconds: 3.2, level: 0.45, peak: 0.6, gate_level: 0.2, too_quiet: false, clipping: false, transcript: 'べんきょう', transcript_note: null }) },
    mic_test_cancel: () => { recStart = null; return ok() },
    mic_test_play: () => ok(),
    cancel_recording: () => { recStart = null; return ok() },
    stop_recording: () => new Promise((resolve) => {
      const card = cards.find((c) => c.id === recCard)!
      recStart = null
      setTimeout(() => {
        if (Math.random() < 0.15) return resolve({ ok: false, error: { code: 'no_speech', message: "Didn't catch that, try again." } })
        const right = Math.random() < 0.6
        if (card.meaning_step && recStep !== 'meaning') {
          heardReading = right ? card.reading : 'おばさん'
          return resolve({ ok: true, data: { stage: 'heard', step: 'reading', card_id: card.id, transcript: heardReading } })
        }
        done.add(card.id)
        const r: ReviewResult = { review_id: ++reviewId, card_id: card.id, deck_id: card.deck_id, transcript: right ? (card.reading ?? card.back) : 'おばさん', score: right ? 3 : 2, score_original: null, overridden: false, is_correct: true, feedback: right ? 'Correct' : `You said おばさん, expected ${card.reading} (long vowel missing)`, expected: { front: card.front, back: card.back, reading: card.reading, alt_answers: card.alt_answers }, state: 'learning', next_due: new Date(Date.now() + 600_000).toISOString(), interval_days: 0, ease_factor: 2.5, recording_id: reviewId, practice: false, steps: null }
        if (card.meaning_step) {
          const rs = heardReading === card.reading ? 3 : 1
          const ms = right ? 3 : 2
          r.steps = [
            { step: 'reading', transcript: heardReading ?? '', score: rs, score_original: null, overridden: false, feedback: rs === 3 ? 'Correct' : `Expected ${card.reading}` },
            { step: 'meaning', transcript: right ? card.back : 'to talk', score: ms, score_original: null, overridden: false, feedback: right ? 'Correct meaning.' : 'Close, but not quite.' },
          ]
          r.score = Math.min(rs, ms) as 1 | 2 | 3
          r.transcript = `${heardReading} / ${r.steps[1].transcript}`
        }
        resolve({ ok: true, data: r })
      }, 900)
    }),
    retry_grading: () => fail('not_found', 'No answer to retry'),
    dont_know: (cardId: number, step?: string) => {
      const card = cards.find((c) => c.id === cardId)!
      if (card.meaning_step && step !== 'meaning') {
        heardReading = ''
        return ok({ stage: 'heard', step: 'reading', card_id: card.id, transcript: '', skipped: true })
      }
      done.add(card.id)
      const skip = { score: 0 as const, score_original: null, overridden: false, feedback: "You didn't know this one.", transcript: '' }
      const r: ReviewResult = { review_id: ++reviewId, card_id: card.id, deck_id: card.deck_id, ...skip, is_correct: false, expected: { front: card.front, back: card.back, reading: card.reading, alt_answers: card.alt_answers }, state: 'learning', next_due: new Date(Date.now() + 60_000).toISOString(), interval_days: 0, ease_factor: 2.5, recording_id: null, practice: false, steps: null }
      if (card.meaning_step) {
        const rs = heardReading === card.reading ? 3 : 0
        r.steps = [
          { step: 'reading', score: rs, score_original: null, overridden: false, transcript: heardReading ?? '', feedback: rs ? 'Correct' : "You didn't know this one." },
          { step: 'meaning', ...skip },
        ]
      }
      return ok(r)
    },
    override_grade: (id: number, correct: boolean) => ok({ review_id: id, score: correct ? 3 : 1, is_correct: correct, overridden: true, score_original: 2 }),
    get_last_review: () => { const last = [...done].pop(); if (last === undefined) return ok(null); const c = cards.find((x) => x.id === last)!; return ok({ review_id: reviewId, card_id: c.id, deck_id: c.deck_id, deck_name: decks.find((d) => d.id === c.deck_id)!.name, front: c.front, reading: c.reading, reviewed_at: now() }) },
    undo_last_review: () => { const last = [...done].pop(); if (last === undefined) return fail('not_found', 'Nothing to undo'); done.delete(last); return ok({ card_id: last, deck_id: cards.find((c) => c.id === last)!.deck_id }) },
    list_reviews: () => ok([]),
    list_recordings: () => ok([]),
    get_weak_spots: (_id: number, days: number) => ok({ days, steps: { reading: { answers: 40, correct: 31 }, meaning: { answers: 40, correct: 36 } }, cards: [
      { card_id: 1, front: '方', reading: 'かた', back: 'person (polite form)', answers: 4, reading_misses: 3, meaning_misses: 0, last_missed_at: now(), recovered: false },
      { card_id: 2, front: '合う', reading: 'あう', back: 'to match', answers: 5, reading_misses: 2, meaning_misses: 1, last_missed_at: now(), recovered: true },
    ] }),
    play_recording: () => ok(),
    stop_playback: () => ok(),
  }
  return impl as unknown as PyApi
}
