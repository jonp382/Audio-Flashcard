import { useCallback, useEffect, useState } from 'react'
import { api, errorMessage, type Deck, type Language } from '../api'
import { formatDue } from '../format'
import { Button, CountBadges, ErrorBanner, Field, PageHeader, Panel, inputCls } from './ui'
import { CustomStudyDialog } from './CustomStudyDialog'
import { LanguagePicker } from './LanguagePicker'

export function DeckList({
  onStudy,
  onCards,
  onSettings,
  onImport,
  onWeakSpots,
}: {
  onStudy: (d: Deck) => void
  onCards: (d: Deck) => void
  onSettings: (d: Deck) => void
  onImport: (d: Deck) => void
  onWeakSpots: (d: Deck) => void
}) {
  const [decks, setDecks] = useState<Deck[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [language, setLanguage] = useState<Language>('ja')
  const [customDeck, setCustomDeck] = useState<Deck | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDecks(await api.listDecks())
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000) // counts change as learning cards come due
    return () => clearInterval(t)
  }, [load])

  const create = async () => {
    try {
      await api.createDeck(name, language)
      setName('')
      setCreating(false)
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const exportDeck = async (d: Deck) => {
    setError(null)
    setNotice(null)
    try {
      const r = await api.exportDeck(d.id)
      if (r) setNotice(`Saved ${d.name} (${r.cards} cards, with progress) to ${r.path}`)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const restoreDeck = async () => {
    setError(null)
    setNotice(null)
    try {
      const r = await api.importDeckFile()
      if (!r) return
      setNotice(`Restored “${r.deck.name}”: ${r.cards} cards and ${r.reviews} reviews.`)
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div>
      <PageHeader
        title="Decks"
        actions={
          <>
            <Button variant="ghost" onClick={restoreDeck} title="Load a deck saved with Export (.afdeck), with its progress, as a new deck">
              Restore deck file…
            </Button>
            <Button variant="primary" onClick={() => setCreating((v) => !v)}>+ New deck</Button>
          </>
        }
      />
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && (
        <div className="mb-4 flex items-center gap-3 rounded-lg bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
          <span className="flex-1 break-all">{notice}</span>
          <button className="text-emerald-600 hover:text-emerald-800" onClick={() => setNotice(null)} aria-label="Dismiss">✕</button>
        </div>
      )}

      {creating && (
        <Panel className="mb-5 space-y-4">
          <Field label="Deck name">
            <input className={inputCls} autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && create()} />
          </Field>
          <LanguagePicker value={language} onChange={setLanguage} />
          <div className="flex gap-2">
            <Button variant="primary" onClick={create} disabled={!name.trim()}>Create</Button>
            <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </Panel>
      )}

      {decks === null ? (
        <p className="text-slate-500">Loading…</p>
      ) : decks.length === 0 ? (
        <Panel className="text-center text-slate-600">
          <p className="mb-2 text-lg">No decks yet.</p>
          <p className="text-sm">Create a deck, then add cards by hand or import a CSV / Anki text export.</p>
        </Panel>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-surface shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2.5">Deck</th>
                <th className="px-4 py-2.5">Answers in</th>
                <th className="px-4 py-2.5" title="New · Learning · Review">Due</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {decks.map((d) => {
                const due = d.counts ? d.counts.new + d.counts.learning + d.counts.review : 0
                return (
                  <tr key={d.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <button className="text-left font-medium text-slate-900 hover:text-indigo-600" onClick={() => onStudy(d)}>
                        {d.name}
                      </button>
                      <div className="text-xs text-slate-400">{d.total_cards ?? 0} cards</div>
                    </td>
                    <td className="px-4 py-3 text-slate-600">{d.language === 'ja' ? 'Japanese' : 'English'}</td>
                    <td className="px-4 py-3">
                      <CountBadges counts={d.counts} />
                      {due === 0 && d.learning_later && d.learning_later.count > 0 && d.learning_later.next_due_at && (
                        <div className="mt-1 text-xs text-slate-500">
                          {d.learning_later.count} learning {d.learning_later.count === 1 ? 'card' : 'cards'} back{' '}
                          {formatDue(d.learning_later.next_due_at)}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        {due > 0 ? (
                          <Button variant="primary" onClick={() => onStudy(d)}>Study</Button>
                        ) : (
                          <Button onClick={() => setCustomDeck(d)} disabled={!d.total_cards} title="Learn more new cards or review today's cards">
                            Study more…
                          </Button>
                        )}
                        <Button variant="ghost" onClick={() => onCards(d)}>Cards</Button>
                        <Button variant="ghost" onClick={() => onImport(d)}>Import</Button>
                        <Button variant="ghost" onClick={() => exportDeck(d)} title="Save this deck with all its progress to a file (.afdeck)">Export</Button>
                        {d.language === 'ja' && (
                          <Button variant="ghost" onClick={() => onWeakSpots(d)} disabled={!d.total_cards}>Weak spots</Button>
                        )}
                        <Button variant="ghost" onClick={() => onSettings(d)} aria-label="Deck settings">⚙</Button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {customDeck && (
        <CustomStudyDialog
          deck={customDeck}
          onClose={() => setCustomDeck(null)}
          onStarted={() => {
            const d = customDeck
            setCustomDeck(null)
            onStudy(d)
          }}
        />
      )}
    </div>
  )
}
