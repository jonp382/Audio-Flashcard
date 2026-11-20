import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  errorMessage,
  isBridgeError,
  type AnswerStep,
  type Card,
  type Counts,
  type Deck,
  type RecordingStatus,
  type ReviewResult,
  type Settings,
  type StepResult,
} from '../api'
import { formatDue, formatDuration, isMac, SCORE_LABEL } from '../format'
import { CustomStudyDialog } from './CustomStudyDialog'
import { ShortcutHelp } from './ShortcutHelp'
import { Button, CountBadges, Sentence, Spinner } from './ui'

type Phase = 'loading' | 'question' | 'recording' | 'grading' | 'result' | 'retry' | 'done'

const POLL_MS = 100
const UNFOCUSED_CLOSE_MS = 120_000

export function ReviewSession({ deck, onExit, onOpenSetup }: { deck: Deck; onExit: () => void; onOpenSetup: () => void }) {
  const [phase, setPhase] = useState<Phase>('loading')
  const [card, setCard] = useState<Card | null>(null)
  const [counts, setCounts] = useState<Counts | undefined>()
  const [nextDue, setNextDue] = useState<string | null>(null)
  const [result, setResult] = useState<ReviewResult | null>(null)
  const [notice, setNotice] = useState<{ kind: 'info' | 'warn' | 'error'; text: string; setup?: boolean } | null>(null)
  const [rec, setRec] = useState<RecordingStatus | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [help, setHelp] = useState(false)
  const [canContinue, setCanContinue] = useState(false)
  const [undoable, setUndoable] = useState(false)
  const [customOpen, setCustomOpen] = useState(false)
  // Two-step cards: which answer is being recorded, and what Whisper heard for the reading.
  const [step, setStep] = useState<AnswerStep>('reading')
  const [heard, setHeard] = useState<string | null>(null)

  // Refs mirror state for the keyboard handler (avoids stale closures).
  const phaseRef = useRef(phase)
  phaseRef.current = phase
  const cardRef = useRef(card)
  cardRef.current = card
  const resultRef = useRef(result)
  resultRef.current = result
  const stepRef = useRef(step)
  stepRef.current = step
  const pending = useRef(false) // a start/stop call is in flight

  const applyNext = useCallback((n: { card: Card | null; counts: Counts; next_learning_due_at: string | null }) => {
    setCounts(n.counts)
    setNextDue(n.next_learning_due_at)
    setResult(null)
    setCanContinue(false)
    setStep('reading')
    setHeard(null)
    if (n.card) {
      setCard(n.card)
      setPhase('question')
    } else {
      setCard(null)
      setPhase('done')
    }
  }, [])

  // --- session lifecycle ---
  useEffect(() => {
    let alive = true
    api.getSettings().then((s) => alive && setSettings(s), () => {})
    api.beginSession(deck.id).then(
      (n) => alive && applyNext(n),
      (e) => {
        if (!alive) return
        setNotice({ kind: 'error', text: errorMessage(e) })
        api.getNextCard(deck.id).then((n) => alive && applyNext(n), () => {})
      },
    )
    return () => {
      alive = false
      api.endSession().catch(() => {})
    }
  }, [deck.id, applyNext])

  // --- mic open/close with window visibility & focus ---
  useEffect(() => {
    let blurTimer: number | undefined
    const onVis = () => {
      if (document.hidden) api.closeMic().catch(() => {})
      else api.openMic().catch(() => {})
    }
    const onBlur = () => {
      blurTimer = window.setTimeout(() => api.closeMic().catch(() => {}), UNFOCUSED_CLOSE_MS)
    }
    const onFocus = () => {
      window.clearTimeout(blurTimer)
      api.openMic().catch(() => {})
    }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', onFocus)
    return () => {
      window.clearTimeout(blurTimer)
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focus', onFocus)
    }
  }, [])

  // --- recording status poll (fast while recording, slow otherwise for the mic indicator) ---
  useEffect(() => {
    let stop = false
    let t: number
    const tick = async () => {
      try {
        const s = await api.getRecordingStatus()
        if (stop) return
        setRec(s)
        if (s.capped && phaseRef.current === 'recording') stopRecording()
      } catch {
        /* ignore */
      }
      if (!stop) t = window.setTimeout(tick, phaseRef.current === 'recording' ? POLL_MS : 1000)
    }
    tick()
    return () => {
      stop = true
      window.clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase === 'recording'])

  // --- "done for now": offer to continue when the next learning card comes due ---
  useEffect(() => {
    if (phase !== 'done' || !nextDue) return
    const ms = new Date(nextDue).getTime() - Date.now()
    const t = window.setTimeout(() => setCanContinue(true), Math.max(0, ms))
    return () => window.clearTimeout(t)
  }, [phase, nextDue])

  // --- actions ---
  const startRecording = useCallback(async () => {
    const c = cardRef.current
    if (!c || pending.current || (phaseRef.current !== 'question' && phaseRef.current !== 'retry')) return
    pending.current = true
    setNotice(null)
    try {
      await api.startRecording(c.id, c.meaning_step ? stepRef.current : undefined)
      setPhase('recording')
    } catch (e) {
      if (isBridgeError(e, 'whisper_not_ready')) setNotice({ kind: 'warn', text: 'Loading speech model… try again in a few seconds.' })
      else if (isBridgeError(e, 'llm_unavailable'))
        setNotice({ kind: 'error', text: 'English answers need ollama, and it is not available.', setup: true })
      else if (!isBridgeError(e, 'busy')) setNotice({ kind: 'error', text: errorMessage(e) })
    } finally {
      pending.current = false
    }
  }, [])

  const stopRecording = useCallback(async () => {
    if (pending.current || phaseRef.current !== 'recording') return
    pending.current = true
    setPhase('grading')
    try {
      const r = await api.stopRecording()
      if ('stage' in r) {
        // Reading step heard (not graded yet): move on to the meaning.
        setHeard(r.transcript)
        setStep('meaning')
        setPhase('question')
        return
      }
      setResult(r)
      setUndoable(true)
      setPhase('result')
    } catch (e) {
      if (isBridgeError(e, 'no_speech')) {
        setNotice({ kind: 'info', text: "Didn't catch that, try again." })
        setPhase('question')
      } else if (isBridgeError(e, 'grading_failed')) {
        setNotice({ kind: 'warn', text: 'The grader gave an unusable answer. Your answer is kept; press Retry.' })
        setPhase('retry')
      } else if (isBridgeError(e, 'llm_unavailable')) {
        setNotice({ kind: 'error', text: 'Could not reach ollama. Start it, then press Retry (your answer is kept).', setup: true })
        setPhase('retry')
      } else {
        setNotice({ kind: 'error', text: errorMessage(e) })
        setPhase('question')
      }
    } finally {
      pending.current = false
    }
  }, [])

  const cancelRecording = useCallback(async () => {
    if (phaseRef.current !== 'recording') return
    await api.cancelRecording().catch(() => {})
    setPhase('question')
  }, [])

  /** "I don't know": the current answer (two-step card: the current step) scores 0. A
   *  skipped reading moves on to the meaning, like a recorded one (heard = ''). */
  const dontKnow = useCallback(async () => {
    const c = cardRef.current
    if (!c || pending.current || phaseRef.current !== 'question') return
    pending.current = true
    setNotice(null)
    try {
      const r = await api.dontKnow(c.id, c.meaning_step ? stepRef.current : undefined)
      if ('stage' in r) {
        setHeard('')
        setStep('meaning')
        return
      }
      setResult(r)
      setUndoable(true)
      setPhase('result')
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    } finally {
      pending.current = false
    }
  }, [])

  const toggleShuffle = useCallback(async () => {
    if (!settings) return
    try {
      setSettings(await api.updateSettings({ shuffle_due: !settings.shuffle_due }))
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }, [settings])

  const retry = useCallback(async () => {
    setPhase('grading')
    setNotice(null)
    try {
      const r = await api.retryGrading()
      setResult(r)
      setUndoable(true)
      setPhase('result')
    } catch (e) {
      if (isBridgeError(e, 'llm_unavailable')) setNotice({ kind: 'error', text: 'ollama is still not reachable.', setup: true })
      else if (isBridgeError(e, 'grading_failed')) setNotice({ kind: 'warn', text: 'Still no usable grade. Try again or re-record.' })
      else setNotice({ kind: 'error', text: errorMessage(e) })
      setPhase(isBridgeError(e, 'not_found') ? 'question' : 'retry')
    }
  }, [])

  const next = useCallback(async () => {
    setNotice(null)
    try {
      applyNext(await api.getNextCard(deck.id))
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }, [deck.id, applyNext])

  const redoReading = useCallback(() => {
    if (phaseRef.current !== 'question' || stepRef.current !== 'meaning') return
    setNotice(null)
    setHeard(null)
    setStep('reading')
  }, [])

  const override = useCallback(async (correct: boolean, s?: AnswerStep) => {
    const r = resultRef.current
    if (!r) return
    try {
      setResult(await api.overrideGrade(r.review_id, correct, r.steps ? s : undefined))
    } catch (e) {
      setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }, [])

  const replay = useCallback(() => {
    const r = resultRef.current
    if (r?.recording_id) api.playRecording(r.recording_id).catch((e) => setNotice({ kind: 'error', text: errorMessage(e) }))
  }, [])

  const undo = useCallback(async () => {
    if (phaseRef.current === 'recording' || phaseRef.current === 'grading') return
    try {
      const u = await api.undoLastReview()
      const [c, n] = await Promise.all([api.getCard(u.card_id), api.getNextCard(deck.id)])
      setCounts(n.counts)
      setResult(null)
      setStep('reading')
      setHeard(null)
      if (c.deck_id === deck.id) {
        setCard(c)
        setPhase('question')
        setNotice({ kind: 'info', text: 'Review undone. Answer this card again.' })
      } else {
        setNotice({ kind: 'info', text: 'Undid a review from another deck.' })
      }
    } catch (e) {
      if (isBridgeError(e, 'not_found')) {
        setUndoable(false)
        setNotice({ kind: 'info', text: 'Nothing to undo.' })
      } else setNotice({ kind: 'error', text: errorMessage(e) })
    }
  }, [deck.id])

  // --- keyboard ---
  useEffect(() => {
    const typing = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
    }
    const hold = () => settings?.record_mode === 'hold'
    const down = (e: KeyboardEvent) => {
      if (typing(e) || customOpen) return
      const p = phaseRef.current
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        if ((isMac && e.metaKey) || (!isMac && e.ctrlKey)) {
          e.preventDefault()
          undo()
        }
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return
      switch (e.key) {
        case ' ':
          e.preventDefault()
          if (e.repeat) return
          if (p === 'recording') {
            if (!hold()) stopRecording()
          } else if (p === 'question' || p === 'retry') startRecording()
          return
        case 'Escape':
          if (help) setHelp(false)
          else cancelRecording()
          return
        case 'd':
        case 'D':
          if (p === 'question') dontKnow()
          return
        case 'Enter':
          if (p === 'result') {
            e.preventDefault()
            next()
          }
          return
        case 'r':
        case 'R':
          if (p === 'result') replay()
          return
        case 'Backspace':
          if (p === 'question' && stepRef.current === 'meaning') {
            e.preventDefault()
            redoReading()
          }
          return
        case '1':
          if (p === 'result') override(false, 'reading')
          return
        case '2':
          if (p === 'result') override(true, 'reading')
          return
        case '3':
          if (p === 'result' && resultRef.current?.steps) override(false, 'meaning')
          return
        case '4':
          if (p === 'result' && resultRef.current?.steps) override(true, 'meaning')
          return
        case '?':
          setHelp((v) => !v)
          return
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.key === ' ' && hold() && phaseRef.current === 'recording' && !typing(e)) {
        e.preventDefault()
        stopRecording()
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [settings, help, customOpen, startRecording, stopRecording, cancelRecording, dontKnow, next, replay, override, undo, redoReading])

  const holdMode = settings?.record_mode === 'hold'
  const twoStep = !!card?.meaning_step
  const askMeaning = twoStep && step === 'meaning'

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col">
      {/* header */}
      <div className="mb-6 flex items-center gap-3">
        <Button variant="ghost" onClick={onExit}>← Decks</Button>
        <h1 className="flex-1 truncate text-lg font-semibold text-slate-900">{deck.name}</h1>
        <MicLive open={!!rec?.mic_open} />
        <CountBadges counts={counts} size="lg" />
        <Button
          variant="ghost"
          onClick={toggleShuffle}
          disabled={!settings}
          aria-pressed={!!settings?.shuffle_due}
          className={settings?.shuffle_due ? 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100' : ''}
          title={
            settings?.shuffle_due
              ? 'Shuffle is on: due cards come in random order (new cards keep their order). Click to turn off.'
              : 'Shuffle is off: due cards come in due order. Click to shuffle them.'
          }
        >
          ⇄ Shuffle
        </Button>
        <Button variant="ghost" onClick={undo} disabled={!undoable || phase === 'recording' || phase === 'grading'} title={`Undo last review (${isMac ? '⌘' : 'Ctrl'}+Z)`}>
          ↶ Undo
        </Button>
        <Button variant="ghost" onClick={() => setHelp(true)} aria-label="Keyboard shortcuts">?</Button>
      </div>

      {notice && (
        <div
          className={`mb-4 flex items-center gap-3 rounded-lg px-4 py-2.5 text-sm ${
            notice.kind === 'info' ? 'bg-sky-50 text-sky-800' : notice.kind === 'warn' ? 'bg-amber-50 text-amber-800' : 'bg-rose-50 text-rose-800'
          }`}
        >
          <span className="flex-1">{notice.text}</span>
          {notice.setup && <Button onClick={onOpenSetup}>ollama setup</Button>}
        </div>
      )}

      {phase === 'loading' && (
        <div className="flex flex-1 items-center justify-center text-slate-500"><Spinner /></div>
      )}

      {phase === 'done' && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <div className="text-4xl">🎉</div>
          <h2 className="text-2xl font-semibold">Done for now</h2>
          {nextDue ? (
            canContinue ? (
              <>
                <p className="text-slate-600">A learning card is due again.</p>
                <Button variant="primary" onClick={next}>Continue</Button>
              </>
            ) : (
              <p className="text-slate-600">1 or more cards due again in {formatDuration(new Date(nextDue).getTime() - Date.now())}. Leave this open to continue then.</p>
            )
          ) : (
            <p className="text-slate-600">No more cards due today in this deck.</p>
          )}
          <div className="mt-2 flex gap-2">
            <Button variant="primary" onClick={() => setCustomOpen(true)}>Continue studying…</Button>
            <Button variant="ghost" onClick={onExit}>Back to decks</Button>
          </div>
        </div>
      )}

      {card && phase !== 'done' && phase !== 'loading' && (
        <div className="flex flex-1 flex-col">
          {/* prompt */}
          <div className="rounded-2xl border border-slate-200 bg-surface px-8 py-12 text-center shadow-sm">
            <div className="mb-2 text-xs uppercase tracking-widest text-slate-400">
              {card.practice ? <span className="text-violet-600">Practice</span> : card.state === 'new' ? 'New' : card.state === 'review' ? 'Review' : 'Learning'} ·{' '}
              {!twoStep
                ? `say it in ${deck.language === 'ja' ? 'Japanese' : 'English'}`
                : phase === 'result'
                  ? 'reading + meaning'
                  : askMeaning
                    ? <span className="text-indigo-600">step 2 of 2: say the meaning in English</span>
                    : <span className="text-indigo-600">step 1 of 2: say the reading</span>}
            </div>
            <div className="jp break-words text-6xl leading-tight text-slate-900">{card.front}</div>
            {card.sentence && (
              <div className="mt-5 break-words text-2xl text-slate-600">
                <Sentence text={card.sentence} word={card.front} />
              </div>
            )}
            {phase === 'result' && card.sentence_meaning && <div className="mt-1 text-sm text-slate-500">{card.sentence_meaning}</div>}
            {phase === 'result' && card.notes && <div className="mx-auto mt-3 max-w-xl text-sm italic text-slate-500">{card.notes}</div>}
            {askMeaning && phase !== 'result' && heard !== null && (
              <div className="mt-6 flex flex-wrap items-center justify-center gap-3 text-slate-600">
                {heard === '' ? (
                  <span className="text-sm text-slate-400">Reading: skipped (I don't know)</span>
                ) : (
                  <>
                    <span className="text-sm text-slate-400">Reading heard:</span>
                    <span className="jp text-2xl text-slate-800">{heard}</span>
                  </>
                )}
                <Button variant="ghost" onClick={redoReading} disabled={phase !== 'question'} title="Re-record the reading (Backspace)">
                  ↺ Re-record
                </Button>
              </div>
            )}
          </div>

          {/* record controls */}
          {phase !== 'result' && (
            <div className="mt-8 flex flex-col items-center gap-4">
              <RecordButton
                phase={phase}
                holdMode={holdMode}
                onStart={startRecording}
                onStop={stopRecording}
              />
              {phase === 'recording' && rec && <LevelMeter rec={rec} />}
              <div className="h-5 text-sm text-slate-500">
                {phase === 'question' && (holdMode ? 'Hold Space (or the button) and speak' : 'Press Space to record, again to stop')}
                {phase === 'recording' && (
                  <span className="flex items-center gap-3">
                    <span>Listening… {holdMode ? 'release to stop' : 'Space to stop'}</span>
                    <button className="text-slate-400 underline hover:text-slate-600" onClick={cancelRecording}>cancel (Esc)</button>
                  </span>
                )}
                {phase === 'grading' && (
                  <span className="flex items-center gap-2"><Spinner /> {twoStep && step === 'reading' ? 'Listening back…' : 'Grading…'}</span>
                )}
              </div>
              {phase === 'question' && (
                <Button variant="ghost" onClick={dontKnow} title="Counts as 0/3 (D)">
                  {!twoStep ? "I don't know" : askMeaning ? "I don't know the meaning" : "I don't know the reading"}
                  <kbd className="rounded border border-slate-300 px-1.5 font-mono text-xs text-slate-400">D</kbd>
                </Button>
              )}
              {phase === 'retry' && (
                <div className="flex gap-2">
                  <Button variant="primary" onClick={retry}>Retry grading</Button>
                </div>
              )}
            </div>
          )}

          {phase === 'result' && result && (
            result.steps ? (
              <StepsResultView result={result} steps={result.steps} onNext={next} onReplay={replay} onOverride={override} onUndo={undo} />
            ) : (
              <ResultView result={result} language={deck.language} onNext={next} onReplay={replay} onOverride={(c) => override(c)} onUndo={undo} />
            )
          )}
        </div>
      )}

      {help && <ShortcutHelp onClose={() => setHelp(false)} />}
      {customOpen && (
        <CustomStudyDialog
          deck={deck}
          onClose={() => setCustomOpen(false)}
          onStarted={(n) => {
            setCustomOpen(false)
            setNotice(null)
            applyNext(n)
          }}
        />
      )}
    </div>
  )
}

function MicLive({ open }: { open: boolean }) {
  return (
    <span
      className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${open ? 'bg-rose-100 text-rose-700' : 'bg-slate-100 text-slate-400'}`}
      title={open ? 'The microphone is open (audio stays in memory, never saved unless you record an answer)' : 'Microphone closed'}
    >
      <span className={`h-2 w-2 rounded-full ${open ? 'animate-pulse bg-rose-500' : 'bg-slate-300'}`} />
      {open ? 'mic live' : 'mic off'}
    </span>
  )
}

function RecordButton({ phase, holdMode, onStart, onStop }: { phase: Phase; holdMode: boolean; onStart: () => void; onStop: () => void }) {
  const recording = phase === 'recording'
  const disabled = phase === 'grading' || phase === 'loading'
  const handlers = holdMode
    ? {
        onPointerDown: () => !recording && onStart(),
        onPointerUp: () => recording && onStop(),
        onPointerLeave: () => recording && onStop(),
      }
    : { onClick: () => (recording ? onStop() : onStart()) }
  return (
    <button
      type="button"
      disabled={disabled}
      {...handlers}
      className={`flex h-24 w-24 items-center justify-center rounded-full text-white shadow-lg transition-all focus:outline-none focus-visible:ring-4 focus-visible:ring-indigo-300 disabled:cursor-not-allowed disabled:opacity-50 ${
        recording ? 'scale-110 bg-rose-600' : 'bg-indigo-600 hover:bg-indigo-500'
      }`}
      aria-label={recording ? 'Stop recording' : 'Start recording'}
    >
      {phase === 'grading' ? <Spinner className="h-8 w-8" /> : recording ? <span className="h-8 w-8 rounded-md bg-white" /> : <MicIcon />}
    </button>
  )
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-10 w-10" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
      <rect x="9" y="3" width="6" height="12" rx="3" fill="currentColor" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  )
}

function LevelMeter({ rec }: { rec: RecordingStatus }) {
  const pct = Math.min(100, Math.round(rec.level * 100))
  const left = Math.max(0, 15 - rec.elapsed_s)
  return (
    <div className="flex w-72 items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full bg-emerald-500 transition-[width] duration-75" style={{ width: `${pct}%` }} />
      </div>
      <span className={`w-14 text-right text-sm tabular-nums ${left < 3 ? 'text-rose-600' : 'text-slate-500'}`}>{rec.elapsed_s.toFixed(1)}s</span>
    </div>
  )
}

function ResultView({
  result,
  language,
  onNext,
  onReplay,
  onOverride,
  onUndo,
}: {
  result: ReviewResult
  language: 'ja' | 'en'
  onNext: () => void
  onReplay: () => void
  onOverride: (correct: boolean) => void
  onUndo: () => void
}) {
  const colors = ['bg-rose-100 text-rose-800', 'bg-rose-100 text-rose-800', 'bg-amber-100 text-amber-800', 'bg-emerald-100 text-emerald-800']
  return (
    <div className="mt-6 space-y-4">
      <div className="flex items-center gap-3">
        <span className={`rounded-full px-3 py-1 text-sm font-semibold ${colors[result.score]}`}>
          {scoreLabel(result.score, result.transcript)} · {result.score}/3
        </span>
        {result.overridden && <span className="text-xs text-slate-500">overridden (auto: {result.score_original})</span>}
        <span className="flex-1 text-slate-700">{result.feedback}</span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-slate-200 bg-surface p-4">
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">You said</div>
          <Said text={result.transcript} className={`${language === 'ja' ? 'jp text-2xl' : 'text-xl'} break-words`} />
        </div>
        <div className="rounded-xl border border-slate-200 bg-surface p-4">
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">Expected</div>
          {language === 'ja' && result.expected.reading ? (
            <>
              <div className="jp break-words text-2xl">{result.expected.reading}</div>
              <div className="text-sm text-slate-500">{result.expected.back}</div>
            </>
          ) : (
            <>
              <div className={`${language === 'ja' ? 'jp text-2xl' : 'text-xl'} break-words`}>{result.expected.back}</div>
              {result.expected.reading && <div className="jp text-sm text-slate-500">{result.expected.reading}</div>}
            </>
          )}
          {result.expected.alt_answers.length > 0 && (
            <div className="mt-1 text-xs text-slate-500">also: <span className="jp">{result.expected.alt_answers.join(' · ')}</span></div>
          )}
        </div>
      </div>

      <div className="text-sm text-slate-500">
        Next review: <strong className="text-slate-700">{formatDue(result.next_due)}</strong>
        {result.practice && (
          <span className="ml-2 text-violet-600">
            · practice: {result.is_correct ? 'schedule unchanged' : 'counted as a lapse'}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onReplay} disabled={!result.recording_id} title="Replay (R)">▶ Replay</Button>
        <Button onClick={() => onOverride(false)} title="I was wrong (1)" className={result.overridden && !result.is_correct ? 'ring-2 ring-rose-400' : ''}>
          ✗ I was wrong
        </Button>
        <Button onClick={() => onOverride(true)} title="I was right (2)" className={result.overridden && result.is_correct ? 'ring-2 ring-emerald-400' : ''}>
          ✓ I was right
        </Button>
        <Button variant="ghost" onClick={onUndo} title="Undo">↶ Undo</Button>
        <span className="flex-1" />
        <Button variant="primary" onClick={onNext} autoFocus title="Next (Enter)">Next →</Button>
      </div>
    </div>
  )
}

const scoreLabel = (score: number, transcript: string) => (!transcript && score === 0 ? "Didn't know" : SCORE_LABEL[score])

/** What was said; an empty transcript means "I don't know" (a real answer is never empty). */
function Said({ text, className }: { text: string; className: string }) {
  if (!text) return <div className="text-base italic text-slate-400">I didn't know</div>
  return <div className={className}>{text}</div>
}

const SCORE_COLORS = ['bg-rose-100 text-rose-800', 'bg-rose-100 text-rose-800', 'bg-amber-100 text-amber-800', 'bg-emerald-100 text-emerald-800']

/** Result for a two-step card: one row per step (each with its own right/wrong buttons),
 *  then the card-level result, which is the lowest step score. */
function StepsResultView({
  result,
  steps,
  onNext,
  onReplay,
  onOverride,
  onUndo,
}: {
  result: ReviewResult
  steps: StepResult[]
  onNext: () => void
  onReplay: () => void
  onOverride: (correct: boolean, step: AnswerStep) => void
  onUndo: () => void
}) {
  const keys: Record<AnswerStep, [string, string]> = { reading: ['1', '2'], meaning: ['3', '4'] }
  return (
    <div className="mt-6 space-y-4">
      {steps.map((s) => {
        const ja = s.step === 'reading'
        const expected = ja ? result.expected.reading : result.expected.back
        const right = s.score >= 2
        const [kWrong, kRight] = keys[s.step]
        return (
          <div key={s.step} className="rounded-xl border border-slate-200 bg-surface p-4">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <span className="w-20 text-xs font-semibold uppercase tracking-wide text-slate-400">{s.step}</span>
              <span className={`rounded-full px-3 py-1 text-sm font-semibold ${SCORE_COLORS[s.score]}`}>
                {scoreLabel(s.score, s.transcript)} · {s.score}/3
              </span>
              {s.overridden && <span className="text-xs text-slate-500">overridden (auto: {s.score_original})</span>}
              <span className="flex-1" />
              <Button onClick={() => onOverride(false, s.step)} title={`${s.step} was wrong (${kWrong})`} className={s.overridden && !right ? 'ring-2 ring-rose-400' : ''}>
                ✗ Wrong
              </Button>
              <Button onClick={() => onOverride(true, s.step)} title={`${s.step} was right (${kRight})`} className={s.overridden && right ? 'ring-2 ring-emerald-400' : ''}>
                ✓ Right
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">You said</div>
                <Said text={s.transcript} className={`${ja ? 'jp text-2xl' : 'text-xl'} break-words`} />
              </div>
              <div>
                <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">Expected</div>
                <div className={`${ja ? 'jp text-2xl' : 'text-xl'} break-words`}>{expected}</div>
                {ja && result.expected.alt_answers.length > 0 && (
                  <div className="mt-1 text-xs text-slate-500">also: <span className="jp">{result.expected.alt_answers.join(' · ')}</span></div>
                )}
              </div>
            </div>
            <p className="mt-2 text-sm text-slate-600">{s.feedback}</p>
          </div>
        )
      })}

      <div className="flex flex-wrap items-center gap-3 text-sm text-slate-500">
        <span className={`rounded-full px-3 py-1 font-semibold ${SCORE_COLORS[result.score]}`}>
          Card: {SCORE_LABEL[result.score]} · {result.score}/3
        </span>
        <span>
          Next review: <strong className="text-slate-700">{formatDue(result.next_due)}</strong>
        </span>
        {result.practice && (
          <span className="text-violet-600">practice: {result.is_correct ? 'schedule unchanged' : 'counted as a lapse'}</span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onReplay} disabled={!result.recording_id} title="Replay both answers (R)">▶ Replay</Button>
        <Button variant="ghost" onClick={onUndo} title="Undo">↶ Undo</Button>
        <span className="flex-1" />
        <Button variant="primary" onClick={onNext} autoFocus title="Next (Enter)">Next →</Button>
      </div>
    </div>
  )
}
