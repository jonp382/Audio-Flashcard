import type { ButtonHTMLAttributes, ReactNode } from 'react'
import type { Counts } from '../api'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'success'

const variants: Record<Variant, string> = {
  primary: 'bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300',
  secondary: 'bg-surface text-slate-700 border border-slate-300 hover:bg-slate-100 disabled:text-slate-400',
  danger: 'bg-rose-600 text-white hover:bg-rose-500 disabled:bg-rose-300',
  success: 'bg-emerald-600 text-white hover:bg-emerald-500 disabled:bg-emerald-300',
  ghost: 'text-slate-600 hover:bg-slate-200/70 disabled:text-slate-300',
}

export function Button({
  variant = 'secondary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    />
  )
}

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-slate-200 bg-surface p-5 shadow-sm ${className}`}>{children}</div>
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

export const inputCls =
  'w-full rounded-lg border border-slate-300 bg-surface px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200'

export function PageHeader({ title, onBack, actions }: { title: ReactNode; onBack?: () => void; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex items-center gap-3">
      {onBack && (
        <Button variant="ghost" onClick={onBack} aria-label="Back">
          ← Back
        </Button>
      )}
      <h1 className="flex-1 text-xl font-semibold text-slate-900">{title}</h1>
      {actions}
    </div>
  )
}

export function ErrorBanner({ message, onClose }: { message: string | null; onClose?: () => void }) {
  if (!message) return null
  return (
    <div className="mb-4 flex items-start gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
      <span className="flex-1">{message}</span>
      {onClose && (
        <button className="text-rose-500 hover:text-rose-700" onClick={onClose} aria-label="Dismiss">
          ✕
        </button>
      )}
    </div>
  )
}

/** Anki-style colours: new blue, learning red, review green. */
export function CountBadges({ counts, size = 'sm' }: { counts?: Counts; size?: 'sm' | 'lg' }) {
  if (!counts) return null
  const cls = size === 'lg' ? 'text-lg font-semibold' : 'text-sm font-medium'
  return (
    <span className={`inline-flex gap-3 tabular-nums ${cls}`}>
      <span className="text-blue-600" title="New">{counts.new}</span>
      <span className="text-rose-600" title="Learning">{counts.learning}</span>
      <span className="text-emerald-600" title="Review">{counts.review}</span>
      {!!counts.practice && <span className="text-violet-600" title="Practice (cards studied today)">+{counts.practice}</span>}
    </span>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return <span className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} />
}

/** Example sentence with the card's word emphasised. Imported sentences mark it with
 *  <b>…</b>; otherwise occurrences of `word` are emphasised. Rendered as text only. */
export function Sentence({ text, word, className = '' }: { text: string; word?: string; className?: string }) {
  let parts: { t: string; b: boolean }[]
  if (/<b>/.test(text)) {
    parts = text.split(/(<b>.*?<\/b>)/).map((t) => (t.startsWith('<b>') ? { t: t.slice(3, -4), b: true } : { t, b: false }))
  } else if (word && text.includes(word)) {
    parts = text.split(word).flatMap((t, i) => (i === 0 ? [{ t, b: false }] : [{ t: word, b: true }, { t, b: false }]))
  } else {
    parts = [{ t: text, b: false }]
  }
  return (
    <span className={`jp ${className}`}>
      {parts.map((p, i) =>
        p.b ? (
          <strong key={i} className="font-semibold text-indigo-700">{p.t.replace(/<\/?b>/g, '')}</strong>
        ) : (
          <span key={i}>{p.t.replace(/<\/?b>/g, '')}</span>
        ),
      )}
    </span>
  )
}
