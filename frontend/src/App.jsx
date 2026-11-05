import { useEffect, useState } from 'react'
import { apiBaseUrl, supabase } from './lib/supabase.js'

export default function App() {
  const [session, setSession] = useState(null)
  const [authReady, setAuthReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [apiIdentity, setApiIdentity] = useState(null)

  useEffect(() => {
    if (!supabase) {
      setAuthReady(true)
      return undefined
    }

    let mounted = true
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (mounted) {
        setSession(nextSession)
        setMessage('')
      }
    })

    supabase.auth
      .getSession()
      .then(({ data: sessionData, error }) => {
        if (!mounted) return
        if (error) setMessage(error.message)
        setSession(sessionData.session)
        setAuthReady(true)
      })
      .catch(() => {
        if (!mounted) return
        setMessage('Could not restore your sign-in session.')
        setAuthReady(true)
      })

    return () => {
      mounted = false
      data.subscription.unsubscribe()
    }
  }, [])

  useEffect(() => {
    if (!session?.access_token) {
      setApiIdentity(null)
      return undefined
    }

    const controller = new AbortController()
    fetch(`${apiBaseUrl}/api/v1/users/me`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}))
          throw new Error(payload.detail || 'Backend could not verify this session.')
        }
        return response.json()
      })
      .then(setApiIdentity)
      .catch((error) => {
        if (error.name !== 'AbortError') setApiIdentity({ error: error.message })
      })

    return () => controller.abort()
  }, [session])

  async function signInWithGoogle() {
    if (!supabase) return
    setBusy(true)
    setMessage('')
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    })
    if (error) setMessage(error.message)
    setBusy(false)
  }

  async function signOut() {
    if (!supabase) return
    setBusy(true)
    const { error } = await supabase.auth.signOut()
    if (error) setMessage(error.message)
    setBusy(false)
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-16 text-slate-100">
      <section className="mx-auto max-w-xl rounded-2xl border border-slate-800 bg-slate-900 p-8 shadow-xl">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-400">FinInsight AI</p>
        <h1 className="mt-4 text-3xl font-bold">Financial statement risk analysis</h1>
        <p className="mt-3 leading-7 text-slate-300">
          Sign in to continue. Your account is managed by Supabase Auth.
        </p>

        {!supabase ? (
          <div className="mt-8 rounded-xl border border-amber-700/60 bg-amber-950/40 p-4 text-sm text-amber-100">
            Add <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_PUBLISHABLE_KEY</code> to
            <code> frontend/.env.local</code>, then restart Vite.
          </div>
        ) : !authReady ? (
          <p className="mt-8 text-sm text-slate-400" role="status">Checking your session…</p>
        ) : session ? (
          <div className="mt-8 space-y-5">
            <div className="rounded-xl border border-slate-700 bg-slate-950/60 p-4">
              <p className="text-sm text-slate-400">Signed in as</p>
              <p className="mt-1 font-medium">{session.user.email || session.user.id}</p>
            </div>
            <div className="rounded-xl border border-slate-700 p-4 text-sm" aria-live="polite">
              <p className="font-semibold">FastAPI session verification</p>
              {apiIdentity?.id ? (
                <p className="mt-1 text-emerald-300">Verified user {apiIdentity.id}</p>
              ) : apiIdentity?.error ? (
                <p className="mt-1 text-amber-300">{apiIdentity.error}</p>
              ) : (
                <p className="mt-1 text-slate-400">Checking signed-in identity…</p>
              )}
            </div>
            <button
              className="rounded-lg border border-slate-600 px-4 py-2 font-medium hover:bg-slate-800 disabled:opacity-60"
              disabled={busy}
              onClick={signOut}
              type="button"
            >
              Sign out
            </button>
          </div>
        ) : (
          <button
            className="mt-8 flex w-full items-center justify-center gap-3 rounded-lg bg-white px-4 py-3 font-semibold text-slate-900 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
            disabled={busy}
            onClick={signInWithGoogle}
            type="button"
          >
            <GoogleMark />
            {busy ? 'Opening Google…' : 'Continue with Google'}
          </button>
        )}

        {message && <p className="mt-4 text-sm text-rose-300" role="alert">{message}</p>}
      </section>
    </main>
  )
}

function GoogleMark() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" viewBox="0 0 48 48">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z" transform="translate(0 4)" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.24 5.48-4.7 7.18l7.2 5.59c4.2-3.88 7.54-9.62 7.54-17.24Z" />
      <path fill="#FBBC05" d="M10.54 28.59a14.4 14.4 0 0 1 0-9.18l-7.98-6.19a23.9 23.9 0 0 0 0 21.56l7.98-6.19Z" transform="translate(0 4)" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.9-5.78l-7.2-5.59c-2 1.35-4.56 2.15-8.7 2.15-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48Z" />
    </svg>
  )
}
