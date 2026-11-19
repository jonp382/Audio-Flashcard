import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, errorMessage, type Card, type Deck, type ReviewHistoryRow } from '../api'
import { formatDue, SCORE_LABEL } from '../format'
import { Button, ErrorBanner, Field, PageHeader, Panel, inputCls } from './ui'

interface Draft {
  front: string
  back: string
  reading: string
  alts: string // one per line
}

const empty: Draft = { front: '', back: '', reading: '', alts: '' }

function toDraft(c: Card): Draft {
  return { front: c.front, back: c.back, reading: c.reading ?? '', alts: c.alt_answers.join('\n') }
}

export function CardEditor({ deckId, onBack }: { deckId: number; onBack: () => void }) {
  const [deck, setDeck] = useState<Deck | null>(null)
  const [cards, setCards] = useState<Card[]>([])
  const [editing, setEditing] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(empty)
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<ReviewHistoryRow[] | null>(null)

  const load = useCallback(async () => {
    try {
      const [d, cs] = await Promise.all([api.getDeck(deckId), api.listCards(deckId)])
      setDeck(d)
      setCards(cs)
    } catch (e) {
      setError(errorMessage(e))
    }
  }, [deckId])

  useEffect(() => {
    load()
  }, [load])

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase()
    if (!f) return cards
    return cards.filter((c) => [c.front, c.back, c.reading ?? '', ...c.alt_answers].some((s) => s.toLowerCase().includes(f)))
  }, [cards, filter])

  const startEdit = async (c: Card | null) => {
    setError(null)
    if (c) {
      setEditing(c.id)
      setDraft(toDraft(c))
      setHistory(null)
      api.listReviews(c.id).then(setHistory, () => setHistory([]))
    } else {
      setEditing('new')
      setDraft(empty)
      setHistory(null)
    }
  }

  const save = async (andNext = false) => {
    setError(null)
    const alt = draft.alts.split('\n').map((s) => s.trim()).filter(Boolean)
    const reading = draft.reading.trim() || null
    try {
      if (editing === 'new') {
        await api.createCard(deckId, draft.front, draft.back, reading, alt)
      } else if (typeof editing === 'number') {
        await api.updateCard(editing, { front: draft.front, back: draft.back, reading, alt_answers: alt })
      }
      await load()
      if (andNext) setDraft(empty)
      else setEditing(null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const del = async (id: number) => {
    try {
      await api.deleteCard(id)
      if (editing === id) setEditing(null)
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div>
      <PageHeader
        title={deck ? `Cards: ${deck.name}` : 'Cards'}
        onBack={onBack}
        actions={<Button variant="primary" onClick={() => startEdit(null)}>+ Add card</Button>}
      />
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="grid grid-cols-5 gap-5">
        <div className="col-span-3 space-y-3">
          <input className={inputCls} placeholder="Search cards…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="max-h-[calc(100vh-14rem)] overflow-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2">Front</th>
                  <th className="px-3 py-2">Back</th>
                  <th className="px-3 py-2">Reading</th>
                  <th className="px-3 py-2">Due</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {shown.map((c) => (
                  <tr
                    key={c.id}
                    className={`cursor-pointer border-t border-slate-100 ${editing === c.id ? 'bg-indigo-50' : 'hover:bg-slate-50'}`}
                    onClick={() => startEdit(c)}
                  >
                    <td className="jp px-3 py-2 text-base">{c.front}</td>
                    <td className="px-3 py-2 text-slate-700">{c.back}</td>
                    <td className="jp px-3 py-2 text-slate-600">{c.reading}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{c.state === 'new' ? 'new' : formatDue(c.due_date ?? c.due_at)}</td>
                    <td className="px-2 py-2 text-right">
                      <button
                        className="text-slate-400 hover:text-rose-600"
                        aria-label="Delete card"
                        onClick={(e) => {
                          e.stopPropagation()
                          del(c.id)
                        }}
                      >
                        🗑
                      </button>
                    </td>
                  </tr>
                ))}
                {shown.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-3 py-6 text-center text-slate-500">No cards.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="col-span-2">
          {editing !== null && (
            <Panel className="space-y-4">
              <h2 className="font-semibold">{editing === 'new' ? 'New card' : 'Edit card'}</h2>
              <Field label="Front (shown as the prompt)">
                <input className={`${inputCls} jp text-base`} autoFocus value={draft.front} onChange={(e) => setDraft({ ...draft, front: e.target.value })} />
              </Field>
              <Field label="Back (expected answer)" hint="Several answers can be separated with 、 , ; / or new lines.">
                <input className={inputCls} value={draft.back} onChange={(e) => setDraft({ ...draft, back: e.target.value })} />
              </Field>
              <Field label="Reading (optional)" hint={deck?.language === 'ja' ? 'For Japanese-answer decks this is what you must say, e.g. はなす.' : 'Helps the grader match kana and kanji.'}>
                <input className={`${inputCls} jp`} value={draft.reading} onChange={(e) => setDraft({ ...draft, reading: e.target.value })} />
              </Field>
              <Field label="Also accept (one per line)">
                <textarea className={`${inputCls} jp`} rows={3} value={draft.alts} onChange={(e) => setDraft({ ...draft, alts: e.target.value })} />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={() => save(false)} disabled={!draft.front.trim() || !draft.back.trim()}>Save</Button>
                {editing === 'new' && (
                  <Button onClick={() => save(true)} disabled={!draft.front.trim() || !draft.back.trim()}>Save & add another</Button>
                )}
                <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
              </div>
              {history && history.length > 0 && (
                <div className="border-t border-slate-200 pt-3">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">History</h3>
                  <ul className="max-h-48 space-y-1 overflow-auto text-xs">
                    {history.map((h) => (
                      <li key={h.id} className="flex gap-2">
                        <span className="text-slate-400 tabular-nums">{new Date(h.reviewed_at).toLocaleString()}</span>
                        <span className={h.is_correct ? 'text-emerald-700' : 'text-rose-700'}>{SCORE_LABEL[h.score]}</span>
                        <span className="jp truncate text-slate-600">“{h.transcript}”</span>
                        {h.overridden && <span className="text-slate-400">(overridden)</span>}
                        {h.recording_id && (
                          <button className="text-indigo-600" onClick={() => api.playRecording(h.recording_id!)}>▶</button>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}
