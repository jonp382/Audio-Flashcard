import { useEffect, useState } from 'react'
import { api, errorMessage, type Deck, type WeakSpots as WeakSpotsData } from '../api'
import { ErrorBanner, PageHeader, Panel, Spinner } from './ui'

const RANGES: { days: number; label: string }[] = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 0, label: 'All time' },
]

type Filter = 'all' | 'reading' | 'meaning'

/** Where two-step answers go wrong: reading vs meaning accuracy and the cards missed most. */
export function WeakSpots({ deck, onBack }: { deck: Deck; onBack: () => void }) {
  const [days, setDays] = useState(30)
  const [filter, setFilter] = useState<Filter>('all')
  const [data, setData] = useState<WeakSpotsData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.getWeakSpots(deck.id, days).then(
      (d) => alive && setData(d),
      (e) => alive && setError(errorMessage(e)),
    )
    return () => {
      alive = false
    }
  }, [deck.id, days])

  const cards = (data?.cards ?? []).filter((c) => filter === 'all' || c[`${filter}_misses`] > 0)
  const answered = data ? data.steps.reading.answers + data.steps.meaning.answers : 0

  return (
    <div>
      <PageHeader title={`Weak spots · ${deck.name}`} onBack={onBack} />
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented options={RANGES.map((r) => ({ value: r.days, label: r.label }))} value={days} onChange={setDays} />
      </div>

      {!data ? (
        <div className="flex justify-center py-16 text-slate-500"><Spinner /></div>
      ) : answered === 0 ? (
        <Panel>
          <p className="text-sm text-slate-600">
            No two-step answers {days ? `in the last ${days} days` : 'yet'}. Weak spots come from cards where you say the reading and then the
            meaning.
          </p>
        </Panel>
      ) : (
        <>
          <div className="mb-5 grid grid-cols-2 gap-4">
            <StatTile label="Reading" correct={data.steps.reading.correct} answers={data.steps.reading.answers} />
            <StatTile label="Meaning" correct={data.steps.meaning.correct} answers={data.steps.meaning.answers} />
          </div>

          <Panel className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="flex-1 font-semibold text-slate-900">Cards you miss</h2>
              <Segmented
                options={[
                  { value: 'all', label: 'All' },
                  { value: 'reading', label: 'Reading' },
                  { value: 'meaning', label: 'Meaning' },
                ]}
                value={filter}
                onChange={setFilter}
              />
            </div>
            {cards.length === 0 ? (
              <p className="text-sm text-slate-500">Nothing missed here. Nice.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="py-2 pr-3 font-medium">Word</th>
                    <th className="py-2 pr-3 font-medium">Meaning</th>
                    <th className="py-2 pr-3 text-right font-medium">Reading missed</th>
                    <th className="py-2 pr-3 text-right font-medium">Meaning missed</th>
                    <th className="py-2 text-right font-medium">Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {cards.map((c) => (
                    <tr key={c.card_id} className="border-t border-slate-100">
                      <td className="py-2 pr-3">
                        <span className="jp text-lg text-slate-900">{c.front}</span>
                        {c.reading && <span className="jp ml-2 text-slate-500">{c.reading}</span>}
                      </td>
                      <td className="py-2 pr-3 text-slate-600">{c.back}</td>
                      <Misses n={c.reading_misses} of={c.answers} />
                      <Misses n={c.meaning_misses} of={c.answers} />
                      <td className="py-2 text-right">
                        {c.recovered ? (
                          <span className="text-emerald-700">✓ right</span>
                        ) : (
                          <span className="text-rose-700">✗ still missing</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="text-xs text-slate-500">
              Counts use your final grades, including “I was right / wrong” overrides. Cards you still miss are listed first.
            </p>
          </Panel>
        </>
      )}
    </div>
  )
}

function StatTile({ label, correct, answers }: { label: string; correct: number; answers: number }) {
  const pct = answers ? Math.round((100 * correct) / answers) : 0
  return (
    <Panel>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label} right</div>
      <div className="mt-1 text-4xl font-semibold tabular-nums text-slate-900">{answers ? `${pct}%` : '—'}</div>
      <div className="mt-1 text-sm text-slate-500 tabular-nums">
        {correct} of {answers} answers
      </div>
    </Panel>
  )
}

function Misses({ n, of }: { n: number; of: number }) {
  return (
    <td className={`py-2 pr-3 text-right tabular-nums ${n ? 'font-semibold text-slate-900' : 'text-slate-300'}`}>
      {n ? `${n} of ${of}` : '—'}
    </td>
  )
}

function Segmented<T extends string | number>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1 text-sm ${o.value === value ? 'bg-indigo-600 text-white' : 'text-slate-600 hover:bg-slate-100'}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
