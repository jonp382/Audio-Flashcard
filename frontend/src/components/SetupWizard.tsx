import { useEffect, useState } from 'react'
import { api, errorMessage, type DownloadStatus, type Status } from '../api'
import { formatBytes } from '../format'
import { Button, ErrorBanner, Panel, Spinner } from './ui'

/**
 * First-launch setup: Whisper model download (once, then offline forever) and the
 * ollama check (skippable; only English-answer decks need it).
 */
export function SetupWizard({
  status,
  onRefresh,
  onDone,
  focusOllama = false,
}: {
  status: Status
  onRefresh: () => Promise<void>
  onDone: () => void
  focusOllama?: boolean
}) {
  const [dl, setDl] = useState<DownloadStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  const whisperBusy = status.whisper_state === 'downloading' || dl?.state === 'downloading'

  useEffect(() => {
    if (!whisperBusy) return
    const t = setInterval(async () => {
      try {
        const s = await api.getDownloadStatus()
        setDl(s)
        if (s.state !== 'downloading') {
          if (s.state === 'error') setError(`Download failed: ${s.error}`)
          await onRefresh()
        }
      } catch (e) {
        setError(errorMessage(e))
      }
    }, 500)
    return () => clearInterval(t)
  }, [whisperBusy, onRefresh])

  // While the model loads after download, keep polling status.
  useEffect(() => {
    if (status.whisper_state !== 'loading') return
    const t = setInterval(onRefresh, 1000)
    return () => clearInterval(t)
  }, [status.whisper_state, onRefresh])

  const startDownload = async () => {
    setError(null)
    try {
      setDl(await api.downloadWhisperModel())
      await onRefresh()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const recheck = async () => {
    setChecking(true)
    try {
      await api.recheckOllama()
      await onRefresh()
    } finally {
      setChecking(false)
    }
  }

  const pct = dl && dl.bytes_total ? Math.floor((dl.bytes_done / dl.bytes_total) * 100) : 0
  const whisperReady = status.whisper_state === 'ready'
  const ollamaOk = status.ollama_reachable && status.llm_model_present

  return (
    <div className="mx-auto max-w-2xl space-y-5 py-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Set up Audio Flashcard</h1>
        <p className="mt-1 text-slate-600">Everything runs on this computer. After setup, no internet connection is needed.</p>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <Panel className={focusOllama ? 'opacity-70' : ''}>
        <div className="flex items-start gap-4">
          <StepDot done={whisperReady} n={1} />
          <div className="flex-1 space-y-3">
            <div>
              <h2 className="font-semibold">Speech recognition model</h2>
              <p className="text-sm text-slate-600">
                Whisper "small" (about 480 MB). This one-time download is the only time the app uses the internet.
              </p>
            </div>
            {status.whisper_state === 'missing' && !whisperBusy && (
              <Button variant="primary" onClick={startDownload}>Download speech model</Button>
            )}
            {whisperBusy && (
              <div className="space-y-1">
                <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                  <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
                </div>
                <div className="text-xs text-slate-500 tabular-nums">
                  {dl ? `${formatBytes(dl.bytes_done)} / ${formatBytes(dl.bytes_total)} (${pct}%)` : 'Starting…'}
                </div>
              </div>
            )}
            {status.whisper_state === 'loading' && (
              <p className="flex items-center gap-2 text-sm text-slate-600"><Spinner /> Loading speech model…</p>
            )}
            {status.whisper_state === 'error' && (
              <div className="space-y-2">
                <p className="text-sm text-rose-700">The model failed to load: {status.whisper_error}</p>
                <Button onClick={startDownload}>Download again</Button>
              </div>
            )}
            {whisperReady && <p className="text-sm text-emerald-700">Ready ({status.whisper_device.toUpperCase()}).</p>}
          </div>
        </div>
      </Panel>

      <Panel>
        <div className="flex items-start gap-4">
          <StepDot done={ollamaOk} n={2} optional />
          <div className="flex-1 space-y-3">
            <div>
              <h2 className="font-semibold">ollama (optional)</h2>
              <p className="text-sm text-slate-600">
                Only needed for decks where you answer <em>in English</em>. Japanese-answer decks are graded without it.
              </p>
            </div>
            {ollamaOk ? (
              <p className="text-sm text-emerald-700">ollama is running and <code>{status.llm_model}</code> is installed.</p>
            ) : !status.ollama_reachable ? (
              <div className="space-y-2 text-sm">
                <p>ollama isn't running (or isn't installed).</p>
                <ol className="list-decimal space-y-1 pl-5 text-slate-700">
                  <li>
                    Install it from{' '}
                    <button className="text-indigo-600 underline" onClick={() => api.openOllamaDownloadPage()}>ollama.com/download</button>{' '}
                    (opens your browser).
                  </li>
                  <li>Start ollama, then run: <Cmd>ollama pull {status.llm_model}</Cmd></li>
                </ol>
              </div>
            ) : (
              <div className="space-y-2 text-sm">
                <p>ollama is running, but the grading model isn't installed yet. In a terminal, run:</p>
                <Cmd>ollama pull {status.llm_model}</Cmd>
              </div>
            )}
            {!ollamaOk && (
              <Button onClick={recheck} disabled={checking}>
                {checking ? <Spinner /> : null} Check again
              </Button>
            )}
          </div>
        </div>
      </Panel>

      <div className="flex justify-end gap-3">
        <Button variant="primary" onClick={onDone} disabled={!whisperReady && !focusOllama}>
          {focusOllama ? 'Back' : ollamaOk ? 'Start learning' : 'Skip for now'}
        </Button>
      </div>
    </div>
  )
}

function Cmd({ children }: { children: React.ReactNode }) {
  return <code className="select-all rounded bg-slate-900 px-2 py-1 font-mono text-xs text-slate-100">{children}</code>
}

function StepDot({ done, n, optional }: { done: boolean; n: number; optional?: boolean }) {
  return (
    <div
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
        done ? 'bg-emerald-100 text-emerald-700' : optional ? 'bg-slate-100 text-slate-500' : 'bg-indigo-100 text-indigo-700'
      }`}
    >
      {done ? '✓' : n}
    </div>
  )
}
