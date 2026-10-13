'use client'

import { LanguageSelector } from '@/components/language-selector'
import { useI18n } from '@/components/i18n-provider'

import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import { authAPI, type AuthSession } from '@/lib/api'

interface AuthState {
  /** The logged-in user, or null when login is off or not yet established. */
  username: string | null
  role: 'administrator' | 'operator' | 'viewer' | null
  canManageAccess: boolean
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState>({ username: null, role: null, canManageAccess: false, logout: async () => {} })

export function useAuth(): AuthState {
  return useContext(AuthContext)
}

/**
 * Blocks the app behind a login form when the instance has user accounts.
 * The gate is presentation only — the API guard is what actually protects
 * every request. An unavailable session probe keeps protected content hidden
 * until the caller can be verified.
 */
export function LoginGate({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [probeFailed, setProbeFailed] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setSession(await authAPI.session())
      setProbeFailed(false)
    } catch {
      setSession(null)
      setProbeFailed(true)
    }
  }, [])

  useEffect(() => {
    // refresh() only sets state after awaiting the session probe; the rule
    // cannot see through the async boundary and assumes the worst.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh()
  }, [refresh])

  useEffect(() => {
    const denied = () => { setSession(null); void refresh() }
    window.addEventListener('camoufox-access-denied', denied)
    return () => window.removeEventListener('camoufox-access-denied', denied)
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

  if (probeFailed) return <SessionUnavailable onRetry={refresh} />

  return (
    <AuthContext.Provider value={{ username: session?.username ?? null, role: session?.role ?? null, canManageAccess: session?.can_manage_access === true, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (session: AuthSession) => void }) {
  const { t } = useI18n()
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
        <LanguageSelector />
        <h1 className="text-[14px] font-semibold">{t('Sign in')}</h1>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">{t('Username')}</span>
          <input
            className="field"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoFocus
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-ink-dim">{t('Password')}</span>
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
          {busy ? t('Signing in…') : t('Sign in')}
        </button>
      </form>
    </div>
  )
}

function SessionUnavailable({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n()
  return <div className="p-6" role="alert"><p>{t('Unable to verify access')}</p><button className="btn btn-default mt-3" onClick={onRetry}>{t('Retry')}</button></div>
}
