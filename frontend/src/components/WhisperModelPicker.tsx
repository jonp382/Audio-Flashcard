import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage, type DownloadStatus, type WhisperModelInfo } from '../api'
import { formatBytes } from '../format'
import { Button, ErrorBanner, Field, inputCls } from './ui'

/**
 * Speech model choice (Settings). Picking an installed model switches to it straight away.
 * Picking one that isn't downloaded offers a download; the current model stays in use
 * until the download finishes, then the app switches over.
 */
export function WhisperModelPicker({ current, onSwitch }: { current: string; onSwitch: (id: string) => Promise<void> }) {
  const [models, setModels] = useState<WhisperModelInfo[]>([])
  // Model chosen in the dropdown but not switched to yet (not downloaded); null = `current`.
  const [pending, setPick] = useState<string | null>(null)
  const pick = pending ?? current
  const [dl, setDl] = useState<DownloadStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setModels(await api.listWhisperModels())
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [])

  useEffect(() => {
    api.listWhisperModels().then(setModels, (e) => setError(errorMessage(e)))
    // Pick up a download that is already running (e.g. started before Settings was opened).
    api.getDownloadStatus().then((s) => {
      if (s.state === 'downloading') {
        setDl(s)
        if (s.model) setPick(s.model)
      }
    }, () => {})
  }, [refresh])

  const busy = dl?.state === 'downloading'
  useEffect(() => {
    if (!busy) return
    const t = setInterval(async () => {
      try {
        const s = await api.getDownloadStatus()
        setDl(s)
        if (s.state === 'done') {
          await refresh()
          if (s.model) await onSwitch(s.model)
          setPick(null)
        } else if (s.state === 'error') {
          setError(`Download failed: ${s.error}`)
        }
      } catch (e) {
        setError(errorMessage(e))
      }
    }, 500)
    return () => clearInterval(t)
  }, [busy, refresh, onSwitch])

  const picked = models.find((m) => m.id === pick)

  const choose = async (id: string) => {
    setError(null)
    const m = models.find((x) => x.id === id)
    if (id === current || m?.installed) {
      setPick(null)
      if (id !== current) await onSwitch(id)
    } else {
      setPick(id)
    }
  }

  const download = async () => {
    if (!picked) return
    setError(null)
    try {
      setDl(await api.downloadWhisperModel(picked.id))
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const pct = dl && dl.bytes_total ? Math.floor((dl.bytes_done / dl.bytes_total) * 100) : 0

  return (
    <div className="space-y-3">
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <Field
        label="Speech model"
        hint={picked ? `${picked.blurb}${picked.custom ? '' : ` ${formatBytes(picked.size_bytes)}.`}` : undefined}
      >
        <select className={inputCls} value={pick} disabled={busy} onChange={(e) => choose(e.target.value)}>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.custom ? ' (your folder)' : m.installed ? '' : ` (not downloaded, ${formatBytes(m.size_bytes)})`}
              {m.id === current ? ' · in use' : ''}
            </option>
          ))}
          {!models.some((m) => m.id === current) && <option value={current}>{current} (missing)</option>}
        </select>
      </Field>
      {picked && !picked.installed && !busy && (
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={download}>Download {picked.label} ({formatBytes(picked.size_bytes)})</Button>
          <span className="text-xs text-slate-500">Keeps using {current} until the download finishes.</span>
        </div>
      )}
      {busy && dl && (
        <div className="space-y-1">
          <div className="h-2 overflow-hidden rounded-full bg-slate-200">
            <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-xs text-slate-500 tabular-nums">
            Downloading {dl.model}: {formatBytes(dl.bytes_done)} / {formatBytes(dl.bytes_total)} ({pct}%)
          </div>
        </div>
      )}
      <p className="text-xs text-slate-500">
        Other faster-whisper models: put the model folder in the app's <code>models</code> folder and it shows up here.
      </p>
    </div>
  )
}
