import { useEffect, useState } from 'react'
import { api, errorMessage, type InputDevice, type Settings, type Status, type Theme } from '../api'
import { applyTheme } from '../theme'
import { MicTest } from './MicTest'
import { Button, ErrorBanner, Field, PageHeader, Panel, inputCls } from './ui'

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '::1']

export function SettingsPage({ status, onBack, onOpenSetup }: { status: Status | null; onBack: () => void; onOpenSetup: () => void }) {
  const [s, setS] = useState<Settings | null>(null)
  const [devices, setDevices] = useState<InputDevice[]>([])
  const [host, setHost] = useState('')
  const [port, setPort] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    api.getSettings().then((x) => {
      setS(x)
      setHost(x.ollama_host)
      setPort(String(x.ollama_port))
    }, (e) => setError(errorMessage(e)))
    api.listInputDevices().then(setDevices, (e) => setError(errorMessage(e)))
  }, [])

  const patch = async (p: Partial<Settings>, label: string) => {
    setError(null)
    try {
      const next = await api.updateSettings(p)
      setS(next)
      applyTheme(next.theme)
      setSaved(label)
      setTimeout(() => setSaved(null), 1500)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  if (!s) return <ErrorBanner message={error} />
  const nonLocal = !LOCAL_HOSTS.includes(host.trim().toLowerCase())

  return (
    <div className="max-w-2xl space-y-5">
      <PageHeader title="Settings" onBack={onBack} />
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {saved && <div className="text-sm text-emerald-700">Saved {saved}</div>}

      <Panel className="space-y-4">
        <h2 className="font-semibold">Appearance</h2>
        <Field label="Theme" hint={s.theme === 'system' ? 'Follows the desktop’s light/dark preference, if the window is told about it.' : undefined}>
          <select className={inputCls} value={s.theme} onChange={(e) => patch({ theme: e.target.value as Theme }, 'theme')}>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="system">Match system</option>
          </select>
        </Field>
      </Panel>

      <Panel className="space-y-4">
        <h2 className="font-semibold">Recording</h2>
        <Field label="Microphone" hint={status?.mic_fallback_used ? 'The saved microphone was not found; using the system default.' : undefined}>
          <select className={inputCls} value={s.mic_device ?? ''} onChange={(e) => patch({ mic_device: e.target.value || null }, 'microphone')}>
            <option value="">System default</option>
            {devices.map((d) => (
              <option key={d.name} value={d.name}>{d.name}{d.is_default ? ' (default)' : ''}</option>
            ))}
            {s.mic_device && !devices.some((d) => d.name === s.mic_device) && <option value={s.mic_device}>{s.mic_device} (not connected)</option>}
          </select>
        </Field>
        <Field label="Record button">
          <div className="flex gap-4 text-sm">
            {(['toggle', 'hold'] as const).map((m) => (
              <label key={m} className="flex items-center gap-2">
                <input type="radio" checked={s.record_mode === m} onChange={() => patch({ record_mode: m }, 'record mode')} />
                {m === 'toggle' ? 'Press to start, press again to stop' : 'Hold to record, release to stop'}
              </label>
            ))}
          </div>
        </Field>
      </Panel>

      <Panel className="space-y-4">
        <h2 className="font-semibold">Microphone test</h2>
        <MicTest
          gain={s.mic_gain}
          onGainChange={async (g) => {
            setS(await api.updateSettings({ mic_gain: g }))
          }}
        />
      </Panel>

      <Panel className="space-y-4">
        <h2 className="font-semibold">Speech recognition</h2>
        <Field
          label="Run Whisper on"
          hint="CPU is the default and keeps all GPU memory free for the LLM. GPU needs CUDA 12 cuBLAS/cuDNN libraries; if they're missing the app falls back to CPU."
        >
          <select className={inputCls} value={s.whisper_device} onChange={(e) => patch({ whisper_device: e.target.value as 'cpu' | 'cuda' }, 'Whisper device (reloading model)')}>
            <option value="cpu">CPU</option>
            <option value="cuda">GPU (CUDA)</option>
          </select>
        </Field>
        {status && (
          <p className="text-xs text-slate-500">
            Model: {status.whisper_state}{status.whisper_state === 'ready' ? ` on ${status.whisper_device.toUpperCase()}` : ''}
          </p>
        )}
      </Panel>

      <Panel className="space-y-4">
        <h2 className="font-semibold">ollama (English-answer decks)</h2>
        <div className="grid grid-cols-3 gap-4">
          <div className="col-span-2">
            <Field label="Host">
              <input className={inputCls} value={host} onChange={(e) => setHost(e.target.value)} />
            </Field>
          </div>
          <Field label="Port">
            <input className={inputCls} type="number" value={port} onChange={(e) => setPort(e.target.value)} />
          </Field>
        </div>
        {nonLocal && (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
            This host is not this computer. Transcripts and card text from English-answer decks will be sent to <strong>{host}</strong>. Audio is never sent.
          </p>
        )}
        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            onClick={() => patch({ ollama_host: host.trim(), ollama_port: Number(port) }, 'ollama address')}
            disabled={host.trim() === s.ollama_host && Number(port) === s.ollama_port}
          >
            Save
          </Button>
          <span className="text-sm text-slate-500">
            {status?.ollama_reachable
              ? status.llm_model_present ? `Connected · ${status.llm_model}` : `Connected · model ${status.llm_model} missing`
              : 'Not reachable'}
          </span>
          <Button variant="ghost" onClick={onOpenSetup}>Setup help</Button>
        </div>
      </Panel>
    </div>
  )
}
