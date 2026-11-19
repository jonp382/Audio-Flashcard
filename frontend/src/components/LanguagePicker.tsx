import type { Language } from '../api'

/** Deck language = the language the ANSWER is spoken in. No auto-detect. */
export function LanguagePicker({ value, onChange }: { value: Language; onChange: (l: Language) => void }) {
  const opt = (l: Language, title: string, desc: string) => (
    <label
      className={`flex flex-1 cursor-pointer items-start gap-3 rounded-lg border p-3 ${
        value === l ? 'border-indigo-500 bg-indigo-50' : 'border-slate-300 hover:bg-slate-50'
      }`}
    >
      <input type="radio" className="mt-1" checked={value === l} onChange={() => onChange(l)} />
      <span>
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-xs text-slate-500">{desc}</span>
      </span>
    </label>
  )
  return (
    <fieldset className="space-y-1">
      <legend className="mb-1 text-sm font-medium text-slate-700">Which language do you speak your answers in?</legend>
      <div className="flex gap-3">
        {opt('ja', 'Answers in Japanese', 'e.g. see 話す, say "はなす". Graded by exact reading match, works offline without ollama.')}
        {opt('en', 'Answers in English', 'e.g. see 話す, say "to speak". Graded by the local LLM (needs ollama).')}
      </div>
    </fieldset>
  )
}
