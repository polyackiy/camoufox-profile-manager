'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Minus, Settings as SettingsIcon } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { useToast } from '@/components/toast'
import {
  getApiKey,
  setApiKey,
  systemAPI,
  type SystemConfig,
  type SystemStatus,
} from '@/lib/api'

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
}

export default function SettingsPage() {
  const [config, setConfig] = useState<SystemConfig | null>(null)
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [key, setKey] = useState('')
  const toast = useToast()

  useEffect(() => {
    setKey(getApiKey())
    async function load() {
      try {
        setConfig(await systemAPI.config())
        setStatus(await systemAPI.status().catch(() => null))
      } catch (err) {
        setError(String(err instanceof Error ? err.message : err))
      }
    }
    load()
  }, [])

  function saveKey(event: React.FormEvent) {
    event.preventDefault()
    setApiKey(key.trim())
    toast(
      'ok',
      key.trim() ? 'API key saved' : 'API key cleared',
      'Stored in this browser only, and sent as X-API-Key.',
    )
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">Settings</h1>
      </header>

      {error ? (
        <EmptyState icon={<SettingsIcon size={18} />} title="Cannot reach the API" body={error} />
      ) : !config ? (
        <p className="px-5 py-8 text-ink-faint">Loading…</p>
      ) : (
        <div className="mx-auto flex max-w-[760px] flex-col gap-6 px-5 py-6">
          {/* Security first: these two decide whether this instance is safe to expose. */}
          <Group
            title="Security"
            note="Configured with environment variables; restart to apply changes."
          >
            <Toggle
              on={config.encryption_enabled}
              label="Proxy password encryption"
              onText="Passwords are encrypted at rest with CPM_SECRET_KEY."
              offText="Passwords are stored as plain text. Set CPM_SECRET_KEY to encrypt them."
              warnWhenOff
            />
            <Toggle
              on={config.user_auth_enabled}
              label="User accounts"
              onText="Login is required. Manage accounts with: camoufox-pm user"
              offText="No user accounts. Create one with: camoufox-pm user add <name>"
              warnWhenOff={config.host !== '127.0.0.1' && !config.api_key_set}
            />
            <Toggle
              on={config.api_key_set}
              label="API key"
              onText="Requests must send a matching X-API-Key header."
              offText={
                config.user_auth_enabled
                  ? 'No API key is set. Machine clients have no way in; humans log in.'
                  : 'No API key is set. Anyone who can reach this port can use the API.'
              }
              warnWhenOff={config.host !== '127.0.0.1' && !config.user_auth_enabled}
            />
            {config.api_key_set && (
              <form onSubmit={saveKey} className="flex items-start gap-4 px-4 py-2.5">
                <span className="w-[150px] shrink-0 text-ink-dim">This browser&apos;s key</span>
                <span className="flex flex-1 gap-2">
                  <input
                    type="password"
                    className="field font-mono"
                    value={key}
                    onChange={(event) => setKey(event.target.value)}
                    placeholder="Paste CPM_API_KEY to keep using this UI"
                    aria-label="API key for this browser"
                    autoComplete="off"
                  />
                  <button type="submit" className="btn btn-default shrink-0">
                    Save
                  </button>
                </span>
              </form>
            )}
            <Row label="Bound to">
              <span className="font-mono">
                {config.host}:{config.port}
              </span>
              {config.host !== '127.0.0.1' && (
                <span className="ml-2 text-danger">reachable beyond this machine</span>
              )}
            </Row>
          </Group>

          <Group title="Instance">
            <Row label="Version">
              <span className="font-mono">{config.version}</span>
            </Row>
            <Row label="Uptime">
              <span className="font-mono">{formatUptime(config.uptime_seconds)}</span>
            </Row>
            <Row label="Database">
              <span className="break-all font-mono text-ink-dim">{config.database_path}</span>
            </Row>
            <Toggle
              on={config.camoufox_available}
              label="Camoufox browser"
              onText="Installed and ready to launch profiles."
              offText="Not installed. Run: camoufox fetch"
              warnWhenOff
            />
          </Group>

          {status && (
            <Group title="Usage">
              <Row label="Profiles">
                <span className="font-mono">{status.total_profiles}</span>
              </Row>
              <Row label="Groups">
                <span className="font-mono">{status.total_groups}</span>
              </Row>
              <Row label="Running browsers">
                <span className="font-mono">{status.running_browsers}</span>
              </Row>
              <Row label="Memory / disk">
                <span className="font-mono text-ink-dim">
                  {Math.round(status.memory_usage)}% / {Math.round(status.disk_usage)}%
                </span>
              </Row>
            </Group>
          )}
        </div>
      )}
    </>
  )
}

function Group({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section>
      <h2 className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">
        {title}
      </h2>
      {note && <p className="mb-2 text-ink-faint">{note}</p>}
      <div className="panel divide-y divide-line">{children}</div>
    </section>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-4 px-4 py-2.5">
      <span className="w-[150px] shrink-0 text-ink-dim">{label}</span>
      <span className="flex-1">{children}</span>
    </div>
  )
}

function Toggle({
  on,
  label,
  onText,
  offText,
  warnWhenOff = false,
}: {
  on: boolean
  label: string
  onText: string
  offText: string
  warnWhenOff?: boolean
}) {
  const warn = !on && warnWhenOff
  return (
    <div className="flex gap-4 px-4 py-2.5">
      <span className="w-[150px] shrink-0 text-ink-dim">{label}</span>
      <span className="flex flex-1 items-start gap-2">
        {on ? (
          <Check size={14} className="mt-0.5 shrink-0 text-ok" />
        ) : warn ? (
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" />
        ) : (
          <Minus size={14} className="mt-0.5 shrink-0 text-ink-faint" />
        )}
        <span className={warn ? 'text-danger' : on ? '' : 'text-ink-dim'}>
          {on ? onText : offText}
        </span>
      </span>
    </div>
  )
}
