import { useEffect, useState } from 'react'
import { api, errorMessage, type CustomStudyOptions, type Deck, type NextCard } from '../api'
import { Button, Spinner } from './ui'

/**
 * "Continue studying" in the SAME deck (no temporary deck): more new cards today and/or
 * a practice pass over the cards already studied today. Everything counts toward the
 * deck's real progress.
 */
export function CustomStudyDialog({ deck, onStarted, onClose }: { deck: Deck; onStarted: (n: NextCard) => void; onClose: () => void }) {
  const [opts, setOpts] = useState<CustomStudyOptions | null>(null)
  const [moreNew, setMoreNew] = useState(true)
  const [count, setCount] = useState('20')
  const [practice, setPractice] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getCustomStudyOptions(deck.id).then((o) => {
      setOpts(o)
      setMoreNew(o.new_available > 0)
      setPractice(o.new_available === 0 && o.studied_today > 0)
      setCount(String(Math.min(20, o.new_available) || 20))
    }, (e) => setError(errorMessage(e)))
  }, [deck.id])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const n = Number(count)
  const countValid = Number.isInteger(n) && n >= 1 && n <= 1000
  const canStart = !!opts && ((moreNew && countValid && opts.new_available > 0) || (practice && opts.studied_today > 0))

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      onStarted(await api.startCustomStudy(deck.id, moreNew ? n : 0, practice))
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40" onClick={onClose}>
      <div className="w-[30rem] space-y-4 rounded-xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div>
          <h2 className="text-lg font-semibold">Continue studying</h2>
          <p className="text-sm text-slate-500">{deck.name}: everything you study here counts toward this deck's normal progress.</p>
        </div>

        {!opts ? (
          error ? <p className="text-sm text-rose-700">{error}</p> : <Spinner />
        ) : (
          <>
            <label className={`flex gap-3 rounded-lg border p-3 ${moreNew ? 'border-indigo-400 bg-indigo-50' : 'border-slate-200'} ${opts.new_available === 0 ? 'opacity-50' : ''}`}>
              <input type="checkbox" className="mt-1" checked={moreNew} disabled={opts.new_available === 0} onChange={(e) => setMoreNew(e.target.checked)} />
              <span className="flex-1 space-y-1">
                <span className="flex items-center gap-2 text-sm font-medium">
                  Learn
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={count}
                    disabled={!moreNew}
                    onChange={(e) => setCount(e.target.value)}
                    className="w-20 rounded-md border border-slate-300 px-2 py-0.5 text-sm"
                  />
                  more new cards
                </span>
                <span className="block text-xs text-slate-500">
                  {opts.new_available > 0
                    ? `${opts.new_available} unseen cards left. Raises today's new-card limit; they're scheduled exactly like normal new cards.`
                    : 'No unseen cards left in this deck.'}
                  {opts.extra_new_today > 0 && ` (Already added ${opts.extra_new_today} extra today.)`}
                </span>
              </span>
            </label>

            <label className={`flex gap-3 rounded-lg border p-3 ${practice ? 'border-violet-400 bg-violet-50' : 'border-slate-200'} ${opts.studied_today === 0 ? 'opacity-50' : ''}`}>
              <input type="checkbox" className="mt-1" checked={practice} disabled={opts.studied_today === 0} onChange={(e) => setPractice(e.target.checked)} />
              <span className="flex-1 space-y-1">
                <span className="block text-sm font-medium">
                  Review the {opts.studied_today} cards from today that are scheduled for later
                </span>
                <span className="block text-xs text-slate-500">
                  Each card once more. Answers are saved to its history. A correct answer keeps its schedule (it's already planned); a miss counts
                  as a real lapse, so the card comes back sooner.
                  {opts.learning_today > 0 &&
                    ` The other ${opts.learning_today} cards you studied today are still in learning; they come back on their own through Study.`}
                </span>
              </span>
            </label>

            {moreNew && !countValid && <p className="text-xs text-rose-600">Enter a number from 1 to 1000.</p>}
            {error && <p className="text-sm text-rose-700">{error}</p>}
          </>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={start} disabled={!canStart || busy}>
            {busy && <Spinner />} Start
          </Button>
        </div>
      </div>
    </div>
  )
}
