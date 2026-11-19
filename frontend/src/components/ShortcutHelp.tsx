import { isMac } from '../format'

const rows: [string, string][] = [
  ['Space', 'Record (toggle mode: press to start/stop · hold mode: hold to record)'],
  ['Esc', 'Cancel the current recording'],
  ['Enter', 'Next card (on the result screen)'],
  ['R', 'Replay your last recording'],
  ['Backspace', 'Re-record the reading (two-step cards, before the meaning)'],
  ['1', 'I was wrong (two-step cards: reading wrong)'],
  ['2', 'I was right (two-step cards: reading right)'],
  ['3', 'Two-step cards: meaning wrong'],
  ['4', 'Two-step cards: meaning right'],
  [isMac ? '⌘ Z' : 'Ctrl Z', 'Undo last review'],
  ['?', 'Show / hide this help'],
]

export function ShortcutHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40" onClick={onClose}>
      <div className="w-[28rem] rounded-xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 text-lg font-semibold">Keyboard shortcuts</h2>
        <table className="w-full text-sm">
          <tbody>
            {rows.map(([k, d]) => (
              <tr key={k}>
                <td className="py-1.5 pr-4">
                  <kbd className="rounded border border-slate-300 bg-slate-50 px-2 py-0.5 font-mono text-xs">{k}</kbd>
                </td>
                <td className="py-1.5 text-slate-700">{d}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-4 text-xs text-slate-500">Shortcuts are ignored while typing in a text field.</p>
      </div>
    </div>
  )
}
