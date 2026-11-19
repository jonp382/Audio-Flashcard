import { useState } from 'react'
import { api, errorMessage, type ImportPreview, type ImportResult } from '../api'
import { Button, ErrorBanner, Field, PageHeader, Panel, Spinner, inputCls } from './ui'

/** pick file -> preview table -> choose columns -> import -> summary */
export function ImportScreen({ deckId, deckName, onBack, onDone }: { deckId: number; deckName: string; onBack: () => void; onDone: () => void }) {
  const [path, setPath] = useState<string | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [front, setFront] = useState(0)
  const [back, setBack] = useState(1)
  const [reading, setReading] = useState<number | null>(null)
  const [sentence, setSentence] = useState<number | null>(null)
  const [sentenceMeaning, setSentenceMeaning] = useState<number | null>(null)
  const [notes, setNotes] = useState<number | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const pick = async () => {
    setError(null)
    setResult(null)
    try {
      const picked = await api.pickImportFile()
      if (!picked) return
      setPath(picked.path)
      const p = await api.importPreview(picked.path)
      setPreview(p)
      // Guess mapping from column names.
      const lower = p.columns.map((c) => c.trim().toLowerCase())
      const find = (...names: string[]) => lower.findIndex((c) => names.includes(c))
      const f = find('front', 'expression', 'kanji', 'word')
      const b = find('back', 'meaning', 'english')
      const r = find('reading', 'kana', 'furigana')
      const s = find('sentence', 'example', 'sentence_ja')
      const sm = find('sentence_meaning', 'sentence translation', 'translation', 'sentence_en')
      setFront(f >= 0 ? f : 0)
      setBack(b >= 0 ? b : Math.min(1, p.columns.length - 1))
      setReading(r >= 0 ? r : null)
      setSentence(s >= 0 ? s : null)
      setSentenceMeaning(sm >= 0 ? sm : null)
      const n = find('notes', 'note', 'comment')
      setNotes(n >= 0 ? n : null)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const doImport = async () => {
    if (!path) return
    setBusy(true)
    setError(null)
    try {
      setResult(await api.importCards(path, deckId, { front, back, reading, sentence, sentence_meaning: sentenceMeaning, notes }))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const colSelect = (value: number | null, onChange: (v: number | null) => void, optional = false) => (
    <select className={inputCls} value={value ?? ''} onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}>
      {optional && <option value="">(none)</option>}
      {preview?.columns.map((c, i) => (
        <option key={i} value={i}>{c}</option>
      ))}
    </select>
  )

  return (
    <div>
      <PageHeader title={`Import into ${deckName}`} onBack={onBack} />
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <Panel className="mb-5 space-y-3">
        <p className="text-sm text-slate-600">
          Accepts <strong>.csv</strong>, <strong>.tsv</strong> and Anki's <strong>Notes in Plain Text (.txt)</strong> export.
          Anki furigana like <span className="jp">話[はな]す</span> is split into front <span className="jp">話す</span> and reading{' '}
          <span className="jp">はなす</span> when no reading column is chosen. Cards whose front and reading already exist in the deck are skipped.
          For Japanese decks, an English back adds a meaning step, and an example sentence helps tell apart words written the same way.
        </p>
        <div className="flex items-center gap-3">
          <Button variant="primary" onClick={pick}>Choose file…</Button>
          {path && <span className="truncate text-sm text-slate-500">{path}</span>}
        </div>
      </Panel>

      {preview && !result && (
        <Panel className="space-y-4">
          <div className="text-xs text-slate-500">
            {preview.total_rows} rows · {preview.detected.encoding} · {preview.detected.delimiter}-separated
            {preview.detected.anki_export && ' · Anki export'}
          </div>
          <div className="grid grid-cols-3 gap-4">
            <Field label="Front column">{colSelect(front, (v) => setFront(v ?? 0))}</Field>
            <Field label="Back column">{colSelect(back, (v) => setBack(v ?? 0))}</Field>
            <Field label="Reading column">{colSelect(reading, setReading, true)}</Field>
            <Field label="Example sentence column">{colSelect(sentence, setSentence, true)}</Field>
            <Field label="Sentence translation column">{colSelect(sentenceMeaning, setSentenceMeaning, true)}</Field>
            <Field label="Notes column">{colSelect(notes, setNotes, true)}</Field>
          </div>
          <div className="max-h-80 overflow-auto rounded-lg border border-slate-200">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  {preview.columns.map((c, i) => (
                    <th key={i} className="px-3 py-2">
                      {c}
                      <span className="ml-1 font-semibold text-indigo-600">
                        {i === front
                          ? '· front'
                          : i === back
                            ? '· back'
                            : i === reading
                              ? '· reading'
                              : i === sentence
                                ? '· sentence'
                                : i === sentenceMeaning
                                  ? '· translation'
                                  : i === notes
                                    ? '· notes'
                                    : ''}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((r, i) => (
                  <tr key={i} className="border-t border-slate-100">
                    {r.map((cell, j) => (
                      <td key={j} className={`jp px-3 py-1.5 ${[front, back, reading, sentence, sentenceMeaning, notes].includes(j) ? '' : 'text-slate-400'}`}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Button variant="primary" onClick={doImport} disabled={busy || front === back}>
            {busy && <Spinner />} Import {preview.total_rows} rows
          </Button>
          {front === back && <p className="text-xs text-rose-600">Front and back must be different columns.</p>}
        </Panel>
      )}

      {result && (
        <Panel className="space-y-3">
          <h2 className="font-semibold">Import finished</h2>
          <ul className="text-sm">
            <li className="text-emerald-700">{result.imported} cards imported</li>
            <li className="text-slate-600">{result.skipped_duplicates} duplicates skipped</li>
          </ul>
          {result.errors.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-slate-600">{result.errors.length} rows skipped</summary>
              <ul className="mt-2 max-h-48 overflow-auto text-xs text-slate-500">
                {result.errors.map((e, i) => (
                  <li key={i}>Row {e.row}: {e.reason}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="flex gap-2">
            <Button variant="primary" onClick={onDone}>Done</Button>
            <Button onClick={pick}>Import another file</Button>
          </div>
        </Panel>
      )}
    </div>
  )
}
