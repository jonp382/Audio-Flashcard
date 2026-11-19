import { useEffect, useState } from 'react'
import { api, errorMessage, type Deck, type Language } from '../api'
import { formatSteps } from '../format'
import { LanguagePicker } from './LanguagePicker'
import { Button, ErrorBanner, Field, PageHeader, Panel, inputCls } from './ui'

export function DeckSettings({ deckId, onBack, onDeleted }: { deckId: number; onBack: () => void; onDeleted: () => void }) {
  const [deck, setDeck] = useState<Deck | null>(null)
  const [name, setName] = useState('')
  const [language, setLanguage] = useState<Language>('ja')
  const [newPerDay, setNewPerDay] = useState('20')
  const [revPerDay, setRevPerDay] = useState('200')
  const [learning, setLearning] = useState('')
  const [relearning, setRelearning] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => {
    api.getDeck(deckId).then((d) => {
      setDeck(d)
      setName(d.name)
      setLanguage(d.language)
      setNewPerDay(String(d.new_per_day))
      setRevPerDay(String(d.reviews_per_day))
      setLearning(formatSteps(d.learning_steps))
      setRelearning(formatSteps(d.relearning_steps))
    }, (e) => setError(errorMessage(e)))
  }, [deckId])

  const save = async () => {
    setError(null)
    const n = Number(newPerDay)
    const r = Number(revPerDay)
    if (!Number.isInteger(n) || n < 0 || !Number.isInteger(r) || r < 0) {
      setError('Daily limits must be whole numbers (0 or more).')
      return
    }
    try {
      const d = await api.updateDeck(deckId, {
        name,
        language,
        new_per_day: n,
        reviews_per_day: r,
        learning_steps: learning,
        relearning_steps: relearning,
      })
      setDeck(d)
      setLearning(formatSteps(d.learning_steps))
      setRelearning(formatSteps(d.relearning_steps))
      setSaved(true)
      setTimeout(() => setSaved(false), 1500)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const del = async () => {
    try {
      await api.deleteDeck(deckId)
      onDeleted()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  if (!deck) return <ErrorBanner message={error} />

  return (
    <div className="max-w-2xl">
      <PageHeader title={`Deck settings: ${deck.name}`} onBack={onBack} />
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <Panel className="space-y-5">
        <Field label="Name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <LanguagePicker value={language} onChange={setLanguage} />
        <div className="grid grid-cols-2 gap-4">
          <Field label="New cards per day">
            <input className={inputCls} type="number" min={0} value={newPerDay} onChange={(e) => setNewPerDay(e.target.value)} />
          </Field>
          <Field label="Maximum reviews per day">
            <input className={inputCls} type="number" min={0} value={revPerDay} onChange={(e) => setRevPerDay(e.target.value)} />
          </Field>
          <Field label="Learning steps" hint='Like Anki: "1m 10m". Units m, h, d. Empty = graduate on first pass.'>
            <input className={inputCls} value={learning} onChange={(e) => setLearning(e.target.value)} />
          </Field>
          <Field label="Relearning steps" hint='Like Anki: "10m". Empty = lapsed cards return tomorrow.'>
            <input className={inputCls} value={relearning} onChange={(e) => setRelearning(e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={save}>Save</Button>
          {saved && <span className="text-sm text-emerald-700">Saved</span>}
        </div>
      </Panel>

      <Panel className="mt-6 border-rose-200">
        <h2 className="font-semibold text-rose-700">Delete deck</h2>
        <p className="mb-3 text-sm text-slate-600">Deletes the deck, all its cards, review history and recordings. This cannot be undone.</p>
        {confirmDelete ? (
          <div className="flex gap-2">
            <Button variant="danger" onClick={del}>Yes, delete "{deck.name}"</Button>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          </div>
        ) : (
          <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete deck…</Button>
        )}
      </Panel>
    </div>
  )
}
