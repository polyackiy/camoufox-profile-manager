'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Layers, Settings, Users } from 'lucide-react'

import { systemAPI } from '@/lib/api'

const NAV = [
  { href: '/', label: 'Profiles', icon: Users },
  { href: '/groups/', label: 'Groups', icon: Layers },
  { href: '/settings/', label: 'Settings', icon: Settings },
]

/** Fixed rail + scrollable work area. The rail never scrolls. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  // trailingSlash is on for the static export, so normalise before comparing.
  const current = pathname.replace(/\/+$/, '') || '/'
  const [address, setAddress] = useState<string | null>(null)

  useEffect(() => {
    // Report where the server actually is rather than assuming loopback.
    systemAPI
      .config()
      .then((config) => setAddress(`${config.host}:${config.port}`))
      .catch(() => setAddress(null))
  }, [])

  return (
    <div className="flex h-screen">
      <aside className="flex w-[196px] shrink-0 flex-col border-r border-line bg-surface">
        <div className="flex h-[52px] items-center gap-2.5 border-b border-line px-4">
          <Wordmark />
        </div>

        <nav className="flex flex-col gap-px p-2">
          {NAV.map(({ href, label, icon: Icon }) => {
            const target = href.replace(/\/+$/, '') || '/'
            const active = current === target
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-2.5 rounded-md px-2.5 py-1.5 transition-colors ${
                  active
                    ? 'bg-raised text-ink'
                    : 'text-ink-dim hover:bg-raised/60 hover:text-ink'
                }`}
              >
                <Icon size={15} strokeWidth={1.75} className={active ? 'text-signal' : ''} />
                {label}
              </Link>
            )
          })}
        </nav>

        <div className="mt-auto px-4 py-3 font-mono text-[11px] text-ink-faint">
          {address ?? '—'}
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  )
}

/**
 * The mark: a stack of three bars narrowing to a point — many identities
 * funnelled through one control surface. Drawn inline, no image request.
 */
function Wordmark() {
  return (
    <>
      <svg width="17" height="17" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <rect x="1" y="2.5" width="14" height="2.4" rx="1.2" fill="var(--color-signal)" />
        <rect x="3" y="6.8" width="10" height="2.4" rx="1.2" fill="var(--color-signal)" opacity="0.62" />
        <rect x="5.5" y="11.1" width="5" height="2.4" rx="1.2" fill="var(--color-signal)" opacity="0.32" />
      </svg>
      <span className="text-[13px] font-semibold tracking-tight">Camoufox</span>
    </>
  )
}
