import { useCallback, useEffect, useState } from 'react'
import { api, type Deck, type Status } from './api'
import { CardEditor } from './components/CardEditor'
import { DeckList } from './components/DeckList'
import { DeckSettings } from './components/DeckSettings'
import { ImportScreen } from './components/ImportScreen'
import { ReviewSession } from './components/ReviewSession'
import { SettingsPage } from './components/SettingsPage'
import { SetupWizard } from './components/SetupWizard'
import { Spinner } from './components/ui'

type View =
  | { name: 'decks' }
  | { name: 'study'; deck: Deck }
  | { name: 'cards'; deckId: number }
  | { name: 'deck-settings'; deckId: number }
  | { name: 'import'; deck: Deck }
  | { name: 'settings' }
  | { name: 'setup'; ollamaOnly: boolean; back: View }

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [view, setView] = useState<View>({ name: 'decks' })
  const [setupDismissed, setSetupDismissed] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.getStatus())
    } catch {
      /* bridge not ready yet */
    }
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  if (!status) {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-slate-500">
        <Spinner /> Starting…
      </div>
    )
  }

  // First launch: the speech model is required before anything can be reviewed.
  const needsWhisper = status.whisper_state === 'missing' || status.whisper_state === 'downloading'
  if ((needsWhisper && !setupDismissed) || view.name === 'setup') {
    const ollamaOnly = view.name === 'setup' && view.ollamaOnly
    return (
      <Shell status={status} onNav={setView} active="setup">
        <SetupWizard
          status={status}
          onRefresh={refresh}
          focusOllama={ollamaOnly}
          onDone={() => {
            setSetupDismissed(true)
            setView(view.name === 'setup' ? view.back : { name: 'decks' })
          }}
        />
      </Shell>
    )
  }

  const goDecks = () => setView({ name: 'decks' })
  const openSetup = () => setView({ name: 'setup', ollamaOnly: status.whisper_state === 'ready', back: view })

  let body
  switch (view.name) {
    case 'study':
      body = <ReviewSession deck={view.deck} onExit={goDecks} onOpenSetup={openSetup} />
      break
    case 'cards':
      body = <CardEditor deckId={view.deckId} onBack={goDecks} />
      break
    case 'deck-settings':
      body = <DeckSettings deckId={view.deckId} onBack={goDecks} onDeleted={goDecks} />
      break
    case 'import':
      body = <ImportScreen deckId={view.deck.id} deckName={view.deck.name} onBack={goDecks} onDone={() => setView({ name: 'cards', deckId: view.deck.id })} />
      break
    case 'settings':
      body = <SettingsPage status={status} onBack={goDecks} onOpenSetup={openSetup} />
      break
    default:
      body = (
        <DeckList
          onStudy={(d) => setView({ name: 'study', deck: d })}
          onCards={(d) => setView({ name: 'cards', deckId: d.id })}
          onSettings={(d) => setView({ name: 'deck-settings', deckId: d.id })}
          onImport={(d) => setView({ name: 'import', deck: d })}
        />
      )
  }

  return (
    <Shell status={status} onNav={setView} active={view.name}>
      {status.whisper_state === 'loading' && view.name !== 'study' && (
        <div className="mb-4 flex items-center gap-2 rounded-lg bg-sky-50 px-4 py-2 text-sm text-sky-800">
          <Spinner /> Loading speech model…
        </div>
      )}
      {body}
    </Shell>
  )
}

function Shell({ status, onNav, active, children }: { status: Status; onNav: (v: View) => void; active: string; children: React.ReactNode }) {
  const studying = active === 'study'
  return (
    <div className="flex h-full flex-col">
      {!studying && (
        <nav className="flex items-center gap-1 border-b border-slate-200 bg-white px-6 py-2.5">
          <span className="mr-4 font-semibold text-indigo-700">Audio Flashcard</span>
          <NavBtn on={active === 'decks'} onClick={() => onNav({ name: 'decks' })}>Decks</NavBtn>
          <NavBtn on={active === 'settings'} onClick={() => onNav({ name: 'settings' })}>Settings</NavBtn>
          <span className="flex-1" />
          {status.mic_open && (
            <span className="flex items-center gap-1.5 text-xs font-medium text-rose-600">
              <span className="h-2 w-2 animate-pulse rounded-full bg-rose-500" /> mic live
            </span>
          )}
        </nav>
      )}
      <main className="flex-1 overflow-auto px-8 py-6">{children}</main>
    </div>
  )
}

function NavBtn({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`rounded-md px-3 py-1.5 text-sm ${on ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-600 hover:bg-slate-100'}`}>
      {children}
    </button>
  )
}
