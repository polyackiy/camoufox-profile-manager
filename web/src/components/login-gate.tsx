'use client'

import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import { authAPI, type AuthSession } from '@/lib/api'

interface AuthState {
  /** The logged-in user, or null when login is off or not yet established. */
  username: string | null
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState>({ username: null, logout: async () => {} })

export function useAuth(): AuthState {
  return useContext(AuthContext)
}

/**
 * Blocks the app behind a login form when the instance has user accounts.
 * The gate is presentation only — the API guard is what actually protects
 * every request — so if the session probe itself fails (API down, old
 * backend), it fails open and lets the API's own errors surface.
 */
export function LoginGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [probeFailed, setProbeFailed] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setSession(await authAPI.session())
      setProbeFailed(false)
    } catch {
      setProbeFailed(true)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const logout = useCallback(async () => {
    await authAPI.logout().catch(() => undefined)
    await refresh()
  }, [refresh])

  if (!session && !probeFailed) {
    // Nothing until the probe answers: flashing the app to a logged-out user
    // would leak nothing (requests would 401) but looks broken.
    return null
  }

  if (session && session.user_auth_enabled && !session.authenticated) {
    return <LoginScreen onLoggedIn={setSession} />
  }

  return (
    <AuthContext.Provider value={{ username: session?.username ?? null, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (session: AuthSession) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onLoggedIn(await authAPI.login(username, password))
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-screen items-center justify-center">
      <form onSubmit={submit} className="panel flex w-[300px] flex-col gap-3 p-5">
        <h1 className="text-[14px] font-semibold">Sign in</h1>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">Username</span>
          <input
            className="field"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">Password</span>
          <input
            type="password"
            className="field"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
        </label>
        {error && <p className="text-danger">{error}</p>}
        <button type="submit" className="btn btn-primary" disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
