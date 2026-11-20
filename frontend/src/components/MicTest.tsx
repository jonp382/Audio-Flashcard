import { useEffect, useRef, useState } from 'react'
import { api, errorMessage, type Language, type MicTestResult, type RecordingStatus } from '../api'
import { Button, Spinner } from './ui'

const TEST_SECONDS = 4
// Energy gate (config.ENERGY_GATE_RMS = 0.004 ≈ -48 dBFS) on the 0..1 meter scale.
// The exact value also comes back from mic_test_stop as gate_level.
const DEFAULT_GATE = 0.2

const toDb = (gain: number) => Math.round(20 * Math.log10(gain))
const fromDb = (db: number) => Math.round(10 ** (db / 20) * 100) / 100

/**
 * Live input meter + gain slider + "record a test and see what Whisper heard".
 * Opens the mic while visible; closes it on unmount.
 */
export function MicTest({ gain, onGainChange }: { gain: number; onGainChange: (g: number) => Promise<void> }) {
  const [rec, setRec] = useState<RecordingStatus | null>(null)
  const [db, setDb] = useState(toDb(gain))
  const [phase, setPhase] = useState<'idle' | 'recording' | 'transcribing'>('idle')
  const [result, setResult] = useState<MicTestResult | null>(null)
  const [language, setLanguage] = useState<Language>('ja')
  const [error, setError] = useState<string | null>(null)
  const [peakHold, setPeakHold] = useState(0)
  const saveTimer = useRef<number | undefined>(undefined)
  const phaseRef = useRef(phase)
  phaseRef.current = phase

  useEffect(() => setDb(toDb(gain)), [gain])

  // Open the mic for the live meter while this panel is mounted.
  useEffect(() => {
    let stop = false
    let t: number
    api.openMic().catch((e) => setError(errorMessage(e)))
    const tick = async () => {
      try {
        const s = await api.getRecordingStatus()
        if (stop) return
        setRec(s)
        setPeakHold((p) => Math.max(s.level, p * 0.96))
        if (phaseRef.current === 'recording' && s.elapsed_s >= TEST_SECONDS) finish()
      } catch {
        /* ignore */
      }
      if (!stop) t = window.setTimeout(tick, 80)
    }
    tick()
    return () => {
      stop = true
      window.clearTimeout(t)
      api.micTestCancel().catch(() => {})
      api.closeMic().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const changeDb = (v: number) => {
    setDb(v)
    window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => onGainChange(fromDb(v)).catch((e) => setError(errorMessage(e))), 150)
  }

  const start = async () => {
    setError(null)
    setResult(null)
    try {
      await api.micTestStart()
      setPhase('recording')
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const finish = async () => {
    if (phaseRef.current !== 'recording') return
    setPhase('transcribing')
    try {
      setResult(await api.micTestStop(language))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setPhase('idle')
    }
  }

  // Meter scale is linear in dB: 0 = -60 dBFS, 1 = 0 dBFS (audio.level_from_rms).
  const levelToDb = (l: number) => (l <= 0 ? '≤ -60' : (l * 60 - 60).toFixed(1))
  const level = rec?.level ?? 0
  const gate = result?.gate_level ?? DEFAULT_GATE
  const verdict = !result
    ? null
    : result.clipping
      ? { cls: 'bg-rose-50 text-rose-800', text: 'Clipping: the signal is distorted. Lower the boost (or move back from the mic).' }
      : result.too_quiet
        ? { cls: 'bg-amber-50 text-amber-800', text: 'Too quiet: answers this quiet are ignored as silence. Raise the boost.' }
        : result.level < gate + 0.12
          ? { cls: 'bg-amber-50 text-amber-800', text: 'A bit quiet. It works, but a little more boost will make recognition more reliable.' }
          : { cls: 'bg-emerald-50 text-emerald-800', text: 'Good level.' }

  return (
    <div className="space-y-4">
      {/* live meter */}
      <div>
        <div className="mb-1 flex justify-between text-xs text-slate-500">
          <span>Live input {rec?.mic_open ? '' : '(mic closed)'}</span>
          {rec?.clipping && <span className="font-semibold text-rose-600">CLIPPING</span>}
        </div>
        <div className="relative h-4 overflow-hidden rounded-full bg-slate-200">
          <div
            className={`h-full transition-[width] duration-75 ${rec?.clipping ? 'bg-rose-500' : level < gate ? 'bg-amber-400' : 'bg-emerald-500'}`}
            style={{ width: `${Math.round(level * 100)}%` }}
          />
          <div className="absolute top-0 h-full w-0.5 bg-slate-500" style={{ left: `${Math.round(peakHold * 100)}%` }} />
          <div className="absolute top-0 h-full border-l-2 border-dashed border-rose-400" style={{ left: `${Math.round(gate * 100)}%` }} title="Below this line counts as silence" />
        </div>
        <div className="mt-1 flex justify-between text-[0.6875rem] text-slate-400">
          <span>silence</span>
          <span>speak normally: aim for the middle, peaks well before the end</span>
          <span>max</span>
        </div>
      </div>

      {/* gain */}
      <div>
        <label className="flex items-center gap-3 text-sm">
          <span className="w-24 font-medium text-slate-700">Mic boost</span>
          <input type="range" min={0} max={24} step={1} value={db} onChange={(e) => changeDb(Number(e.target.value))} className="flex-1 accent-indigo-600" />
          <span className="w-16 text-right tabular-nums text-slate-600">+{db} dB</span>
        </label>
        <p className="mt-1 text-xs text-slate-500">
          Software boost applied to everything the app records. If you have to lean into the mic, raise this instead; being too close causes popping
          that blurs sounds like b and p.
        </p>
      </div>

      {/* test recording */}
      <div className="rounded-lg border border-slate-200 p-4">
        <div className="flex flex-wrap items-center gap-3">
          {phase === 'recording' ? (
            <Button variant="danger" onClick={finish}>■ Stop ({Math.max(0, TEST_SECONDS - (rec?.elapsed_s ?? 0)).toFixed(0)}s)</Button>
          ) : (
            <Button variant="primary" onClick={start} disabled={phase === 'transcribing'}>
              {phase === 'transcribing' ? <Spinner /> : '●'} Record a {TEST_SECONDS}s test
            </Button>
          )}
          <select className="rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
            <option value="ja">Recognize as Japanese</option>
            <option value="en">Recognize as English</option>
          </select>
          <Button onClick={() => api.micTestPlay().catch((e) => setError(errorMessage(e)))} disabled={!result || phase !== 'idle'}>
            ▶ Play back
          </Button>
        </div>
        <p className="mt-2 text-xs text-slate-500">Say a few words at your normal distance. The clip stays in memory and is never saved.</p>

        {error && <p className="mt-3 text-sm text-rose-700">{error}</p>}
        {result && (
          <div className="mt-3 space-y-2">
            {verdict && <div className={`rounded-md px-3 py-2 text-sm ${verdict.cls}`}>{verdict.text}</div>}
            <div className="text-xs tabular-nums text-slate-500">
              Clip level {levelToDb(result.level)} dBFS · peak {result.peak.toFixed(3)} · silence cutoff {levelToDb(result.gate_level)} dBFS
            </div>
            <div className="text-sm">
              <span className="text-slate-500">Whisper heard: </span>
              {result.transcript ? <span className="jp text-lg">{result.transcript}</span> : <span className="text-slate-500 italic">{result.transcript_note}</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
