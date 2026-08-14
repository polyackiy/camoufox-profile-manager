'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Ellipsis,
  Globe,
  PackageOpen,
  Pencil,
  Play,
  Plus,
  Search,
  Square,
  Trash2,
  Upload,
  Users,
} from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { ConfirmDialog, Modal } from '@/components/modal'
import { ProfileForm } from '@/components/profile-form'
import { useToast } from '@/components/toast'
import {
  browsersAPI,
  formatLastUsed,
  formatProxyString,
  groupsAPI,
  hasGeography,
  OS_LABELS,
  profilesAPI,
  type Group,
  type Profile,
} from '@/lib/api'

type SortKey = 'name' | 'id' | 'group' | 'os' | 'status' | 'last_used'

const COLUMNS: { key: SortKey; label: string; className?: string }[] = [
  { key: 'name', label: 'Name' },
  { key: 'id', label: 'ID' },
  { key: 'group', label: 'Group' },
  { key: 'os', label: 'OS' },
  { key: 'status', label: 'Status' },
  { key: 'last_used', label: 'Last used' },
]

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [groups, setGroups] = useState<Group[]>([])
  const [running, setRunning] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortKey, setSortKey] = useState<SortKey>('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [page, setPage] = useState(1)
  const perPage = 25

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Profile | null>(null)
  const [confirm, setConfirm] = useState<null | {
    title: string
    body: string
    label: string
    run: () => Promise<void>
  }>(null)
  const [exportOpen, setExportOpen] = useState(false)

  const toast = useToast()
  const importRef = useRef<HTMLInputElement>(null)
  const archiveRef = useRef<HTMLInputElement>(null)

  const loadProfiles = useCallback(async () => {
    try {
      setError(null)
      // The API paginates; pull every page so sorting and search stay client-side
      // and instant. Guarded so a bad has_next can never spin forever.
      const collected: Profile[] = []
      for (let pageNumber = 1; pageNumber <= 200; pageNumber += 1) {
        const response = await profilesAPI.getProfiles({ page: pageNumber, per_page: 100 })
        collected.push(...response.profiles)
        if (!response.has_next) break
      }
      setProfiles(collected)
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadRunning = useCallback(async () => {
    try {
      const response = await browsersAPI.active()
      setRunning(new Set(response.active_browsers.map((browser) => browser.profile_id)))
    } catch {
      // The poll is best-effort; a transient failure must not surface as an error.
    }
  }, [])

  const loadGroups = useCallback(async () => {
    try {
      const response = await groupsAPI.list()
      setGroups(response.groups)
    } catch {
      // Groups are optional context for the table and the form.
    }
  }, [])

  useEffect(() => {
    // loadProfiles clears the previous error synchronously before awaiting,
    // which is the one render the rule objects to and is what makes a reload
    // stop showing a stale failure.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadProfiles()
    loadGroups()
    loadRunning()
    const timer = setInterval(loadRunning, 5000)
    return () => clearInterval(timer)
  }, [loadProfiles, loadGroups, loadRunning])

  useEffect(() => {
    // Close on any click that is not on a trigger or inside an open menu.
    // Relying on stopPropagation in the trigger's React handler is not enough:
    // React delegates to its root container, so the document listener could
    // still fire and shut the menu in the same click that opened it.
    const close = (event: MouseEvent) => {
      const target = event.target as Element | null
      if (target?.closest('[data-menu-trigger], [role="menu"]')) return
      setMenuFor(null)
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])

  // Go back to the first page when the filters change. Adjusted during render
  // rather than in an effect: an effect would paint the new results clamped to
  // whatever page number survived, then snap to the first one, and React
  // documents this as the way to derive state from a change in props or state.
  const [filterForPage, setFilterForPage] = useState(() => ({ search, statusFilter }))
  if (filterForPage.search !== search || filterForPage.statusFilter !== statusFilter) {
    setFilterForPage({ search, statusFilter })
    setPage(1)
  }

  const groupNames = useMemo(
    () => new Map(groups.map((group) => [group.id, group.name])),
    [groups],
  )

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const filtered = profiles.filter((profile) => {
      if (statusFilter !== 'all' && profile.status !== statusFilter) return false
      if (!needle) return true
      return (
        profile.name.toLowerCase().includes(needle) || profile.id.toLowerCase().includes(needle)
      )
    })

    const direction = sortDir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case 'id':
          return a.id.localeCompare(b.id) * direction
        case 'group':
          return (groupNames.get(a.group ?? '') ?? '').localeCompare(
            groupNames.get(b.group ?? '') ?? '',
          ) * direction
        case 'os':
          return (a.browser_settings?.os ?? '').localeCompare(b.browser_settings?.os ?? '') * direction
        case 'status':
          return a.status.localeCompare(b.status) * direction
        case 'last_used':
          return (
            (new Date(a.last_used ?? 0).getTime() - new Date(b.last_used ?? 0).getTime()) * direction
          )
        default:
          return a.name.localeCompare(b.name) * direction
      }
    })
  }, [profiles, search, statusFilter, sortKey, sortDir, groupNames])

  // Offering the bulk clear when nothing selected would change is just noise.
  const selectedWithGeography = useMemo(
    () => profiles.filter((profile) => selected.has(profile.id) && hasGeography(profile)).length,
    [profiles, selected],
  )

  const totalPages = Math.max(1, Math.ceil(visible.length / perPage))
  // Deleting the last rows of a page must not strand the user on an empty one.
  const currentPage = Math.min(page, totalPages)
  const pageRows = visible.slice((currentPage - 1) * perPage, currentPage * perPage)
  const allOnPageSelected = pageRows.length > 0 && pageRows.every((row) => selected.has(row.id))

  /** Close the row menu and hand focus back to the button that opened it.
   *
   * Focus moves first: removing the menu while one of its items still has focus
   * drops focus to <body>, and a refocus queued after the close races that reset.
   */
  function closeMenu(profileId: string) {
    document.querySelector<HTMLButtonElement>(`[data-menu-trigger="${profileId}"]`)?.focus()
    setMenuFor(null)
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  async function withBusy(id: string, action: () => Promise<void>) {
    setBusy((current) => new Set(current).add(id))
    try {
      await action()
    } finally {
      setBusy((current) => {
        const next = new Set(current)
        next.delete(id)
        return next
      })
    }
  }

  async function launch(profile: Profile) {
    await withBusy(profile.id, async () => {
      try {
        await profilesAPI.startProfile(profile.id)
        await Promise.all([loadRunning(), loadProfiles()])
      } catch (err) {
        toast('error', 'Could not launch browser', String(err))
      }
    })
  }

  async function stop(profile: Profile) {
    await withBusy(profile.id, async () => {
      try {
        await profilesAPI.closeProfile(profile.id)
        await loadRunning()
      } catch (err) {
        toast('error', 'Could not close browser', String(err))
      }
    })
  }

  function download(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  async function exportArchive(profile: Profile) {
    toast('info', 'Packing the profile…', 'Cookies and history make this take a moment.')
    try {
      const blob = await profilesAPI.exportArchive(profile.id)
      const safe = profile.name.replace(/[^A-Za-z0-9_.-]+/g, '-').replace(/^-|-$/g, '')
      download(blob, `${safe || profile.id}.camoufox.zip`)
      toast(
        'ok',
        'Profile exported',
        'The archive holds session cookies and the proxy password — keep it like a password.',
      )
    } catch (err) {
      toast('error', 'Could not export the profile', String(err))
    }
  }

  async function importArchive(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const profile = await profilesAPI.importArchive(file)
      toast('ok', 'Profile imported', profile.name)
      loadProfiles()
    } catch (err) {
      toast('error', 'Could not import the profile', String(err))
    }
  }

  async function clone(profile: Profile) {
    try {
      await profilesAPI.cloneProfile(profile.id, `${profile.name} copy`)
      toast('ok', 'Profile cloned', `${profile.name} copy`)
      loadProfiles()
    } catch (err) {
      toast('error', 'Could not clone profile', String(err))
    }
  }

  /**
   * A proxy that answers is only half the question: the toast also carries the
   * first thing a page could notice between the exit address and the profile,
   * because a healthy proxy in the wrong country is still a giveaway.
   */
  async function checkProxy(profile: Profile) {
    toast('info', 'Checking the proxy…', profile.name)
    try {
      const result = await profilesAPI.checkProxy(profile.id)
      if (!result.reachable) {
        toast('error', `${profile.name}: proxy unreachable`, result.error ?? undefined)
        return
      }
      const where = [result.location?.country, result.location?.timezone]
        .filter(Boolean)
        .join(' · ')
      const problem = result.findings.find((finding) => finding.level !== 'info')
      const summary = `${result.location?.ip}${where ? ` — ${where}` : ''}${
        result.latency_ms !== null ? ` · ${result.latency_ms} ms` : ''
      }`
      if (problem) toast('error', `${profile.name}: ${summary}`, problem.message)
      else toast('ok', `${profile.name}: ${summary}`)
    } catch (err) {
      toast('error', 'Could not check the proxy', String(err))
    }
  }

  function askDelete(profile: Profile) {
    setConfirm({
      title: 'Delete profile',
      body: `"${profile.name}" and its browser data will be removed. This cannot be undone.`,
      label: 'Delete',
      run: async () => {
        await profilesAPI.deleteProfile(profile.id)
        toast('ok', 'Profile deleted', profile.name)
        setSelected((current) => {
          const next = new Set(current)
          next.delete(profile.id)
          return next
        })
        loadProfiles()
      },
    })
  }

  function askBulkDelete() {
    const count = selected.size
    setConfirm({
      title: `Delete ${count} profile${count === 1 ? '' : 's'}`,
      body: 'The selected profiles and their browser data will be removed. This cannot be undone.',
      label: 'Delete',
      run: async () => {
        for (const id of selected) await profilesAPI.deleteProfile(id)
        toast('ok', `Deleted ${count} profile${count === 1 ? '' : 's'}`)
        setSelected(new Set())
        loadProfiles()
      },
    })
  }

  /**
   * The profiles this exists for were all created the same way — with the
   * timezone and coordinates of a randomly chosen region — so the useful unit is
   * a selection, not one profile at a time. Only those that actually state a
   * location are named, so the confirmation says what will really change.
   */
  function askClearGeography() {
    const ids = profiles.filter((p) => selected.has(p.id) && hasGeography(p)).map((p) => p.id)
    setConfirm({
      title: `Clear geography of ${ids.length} profile${ids.length === 1 ? '' : 's'}`,
      body:
        'The stored timezone and coordinates are unset, so Camoufox derives both — and the ' +
        'WebRTC address — from where each profile\'s proxy comes out, as a profile created ' +
        'today does. Languages and the pinned machine are untouched.',
      label: 'Clear',
      run: async () => {
        const result = await profilesAPI.clearGeography(ids)
        toast('ok', `Cleared ${result.cleared.length} profile${result.cleared.length === 1 ? '' : 's'}`)
        setSelected(new Set())
        loadProfiles()
      },
    })
  }

  function askCloseAll() {
    setConfirm({
      title: 'Close all browsers',
      body: `${running.size} running browser${running.size === 1 ? '' : 's'} will be closed.`,
      label: 'Close all',
      run: async () => {
        const result = await browsersAPI.closeAll()
        toast('ok', result.message)
        loadRunning()
      },
    })
  }

  async function runConfirmed() {
    if (!confirm) return
    const action = confirm
    setConfirm(null)
    try {
      await action.run()
    } catch (err) {
      toast('error', 'Action failed', String(err))
    }
  }

  async function exportExcel() {
    setExportOpen(false)
    try {
      const blob = await profilesAPI.exportExcel()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'camoufox-profiles.xlsx'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      toast('ok', 'Exported to camoufox-profiles.xlsx', 'The file contains proxy passwords in clear text.')
    } catch (err) {
      toast('error', 'Export failed', String(err))
    }
  }

  async function importExcel(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const result = await profilesAPI.importExcel(file)
      if (result.success) {
        toast('ok', `Imported ${result.data?.created_count ?? 0} profiles`)
        loadProfiles()
      } else {
        toast(
          'error',
          'Import finished with errors',
          (result.data?.errors ?? []).slice(0, 3).join('\n') || result.message,
        )
      }
    } catch (err) {
      toast('error', 'Import failed', String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">Profiles</h1>
        <span className="font-mono text-ink-faint">{visible.length}</span>

        <div className="relative ml-3 w-[240px]">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <input
            className="field h-[30px] pl-7"
            placeholder="Search name or ID"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search profiles"
          />
        </div>

        <select
          className="field h-[30px] w-[132px]"
          value={statusFilter}
          onChange={(event) => setStatusFilter(event.target.value)}
          aria-label="Filter by status"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="blocked">Blocked</option>
          <option value="maintenance">Maintenance</option>
        </select>

        <div className="ml-auto flex items-center gap-2">
          {running.size > 0 && (
            <button className="btn btn-default" onClick={askCloseAll}>
              <Square size={12} fill="currentColor" />
              Close {running.size} running
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => setExportOpen(true)} title="Export to Excel">
            <Download size={14} />
          </button>
          <button
            className="btn btn-ghost"
            onClick={() => importRef.current?.click()}
            title="Import from Excel"
          >
            <Upload size={14} />
          </button>
          <input
            ref={importRef}
            type="file"
            accept=".xlsx,.xls"
            onChange={importExcel}
            className="hidden"
          />
          <button
            className="btn btn-ghost"
            onClick={() => archiveRef.current?.click()}
            title="Import a profile archive"
          >
            <PackageOpen size={14} />
          </button>
          <input
            ref={archiveRef}
            type="file"
            accept=".zip"
            onChange={importArchive}
            className="hidden"
          />
          <button
            className="btn btn-primary"
            onClick={() => {
              setEditing(null)
              setFormOpen(true)
            }}
          >
            <Plus size={14} strokeWidth={2.5} />
            New profile
          </button>
        </div>
      </header>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-b border-line bg-raised px-5 py-2">
          <span>{selected.size} selected</span>
          {selectedWithGeography > 0 && (
            <button className="btn btn-default h-7" onClick={askClearGeography}>
              <Globe size={13} />
              Clear geography ({selectedWithGeography})
            </button>
          )}
          <button className="btn btn-danger h-7" onClick={askBulkDelete}>
            <Trash2 size={13} />
            Delete
          </button>
          <button className="btn btn-ghost h-7" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}

      {error ? (
        <EmptyState
          icon={<Users size={18} />}
          title="Cannot reach the API"
          body={error}
          action={
            <button className="btn btn-default" onClick={loadProfiles}>
              Retry
            </button>
          }
        />
      ) : loading ? (
        <p className="px-5 py-8 text-ink-faint">Loading…</p>
      ) : profiles.length === 0 ? (
        <EmptyState
          icon={<Users size={18} />}
          title="No profiles yet"
          body="A profile is one isolated browser identity — its own fingerprint, proxy, cookies and storage. Create one to get started."
          action={
            <button
              className="btn btn-primary"
              onClick={() => {
                setEditing(null)
                setFormOpen(true)
              }}
            >
              <Plus size={14} strokeWidth={2.5} />
              Create your first profile
            </button>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<Search size={18} />}
          title="No matches"
          body="No profile matches the current search and filter."
          action={
            <button
              className="btn btn-default"
              onClick={() => {
                setSearch('')
                setStatusFilter('all')
              }}
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="w-9 py-2 pl-5">
                <input
                  type="checkbox"
                  className="accent-signal"
                  checked={allOnPageSelected}
                  aria-label="Select all on this page"
                  onChange={() =>
                    setSelected((current) => {
                      const next = new Set(current)
                      pageRows.forEach((row) =>
                        allOnPageSelected ? next.delete(row.id) : next.add(row.id),
                      )
                      return next
                    })
                  }
                />
              </th>
              {COLUMNS.map((column) => (
                <th key={column.key} className="py-2 pr-4 font-medium">
                  <button
                    className="inline-flex items-center gap-1 hover:text-ink"
                    onClick={() => toggleSort(column.key)}
                  >
                    {column.label}
                    {sortKey === column.key &&
                      (sortDir === 'asc' ? <ArrowUp size={11} /> : <ArrowDown size={11} />)}
                  </button>
                </th>
              ))}
              <th className="py-2 pr-4 font-medium">Proxy</th>
              <th className="w-[104px] py-2 pr-5" />
            </tr>
          </thead>
          <tbody>
            {pageRows.map((profile, index) => {
              const isRunning = running.has(profile.id)
              const isBusy = busy.has(profile.id)
              return (
                <tr
                  key={profile.id}
                  className="row-in group border-b border-line/60 hover:bg-surface"
                  style={{ animationDelay: `${Math.min(index, 12) * 12}ms` }}
                >
                  <td className="relative py-2.5 pl-5">
                    {/* Hairline marker: a running profile is visible at a glance. */}
                    {isRunning && (
                      <span className="absolute left-0 top-0 h-full w-[2px] bg-signal" aria-hidden />
                    )}
                    <input
                      type="checkbox"
                      className="accent-signal"
                      checked={selected.has(profile.id)}
                      aria-label={`Select ${profile.name}`}
                      onChange={() =>
                        setSelected((current) => {
                          const next = new Set(current)
                          if (next.has(profile.id)) next.delete(profile.id)
                          else next.add(profile.id)
                          return next
                        })
                      }
                    />
                  </td>

                  <td className="py-2.5 pr-4 font-medium">{profile.name}</td>
                  <td className="py-2.5 pr-4 font-mono text-ink-faint">{profile.id}</td>
                  <td className="py-2.5 pr-4 text-ink-dim">
                    {profile.group ? (groupNames.get(profile.group) ?? profile.group) : '—'}
                  </td>
                  <td className="py-2.5 pr-4 text-ink-dim">
                    {OS_LABELS[profile.browser_settings?.os ?? ''] ?? profile.browser_settings?.os}
                  </td>
                  <td className="py-2.5 pr-4">
                    <StatusCell status={profile.status} running={isRunning} />
                  </td>
                  <td className="py-2.5 pr-4 text-ink-dim">{formatLastUsed(profile.last_used)}</td>
                  <td className="py-2.5 pr-4 font-mono text-ink-faint">
                    {formatProxyString(profile.proxy_config) || '—'}
                  </td>

                  <td className="py-2.5 pr-5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        className="btn btn-default h-7"
                        disabled={isBusy}
                        onClick={() => (isRunning ? stop(profile) : launch(profile))}
                      >
                        {isRunning ? (
                          <>
                            <Square size={11} fill="currentColor" />
                            Stop
                          </>
                        ) : (
                          <>
                            <Play size={11} fill="currentColor" />
                            Run
                          </>
                        )}
                      </button>

                      <div className="relative">
                        <button
                          className="btn btn-ghost h-7 w-7 p-0"
                          aria-label={`Actions for ${profile.name}`}
                          aria-haspopup="menu"
                          aria-expanded={menuFor === profile.id}
                          data-menu-trigger={profile.id}
                          onClick={(event) => {
                            event.stopPropagation()
                            setMenuFor(menuFor === profile.id ? null : profile.id)
                          }}
                        >
                          <Ellipsis size={15} />
                        </button>

                        {menuFor === profile.id && (
                          <div
                            role="menu"
                            className="dialog-in absolute right-0 top-8 z-30 w-[164px] overflow-hidden rounded-md border border-line bg-raised py-1 shadow-xl shadow-black/50"
                            onClick={(event) => event.stopPropagation()}
                            onKeyDown={(event) => {
                              const items = Array.from(
                                event.currentTarget.querySelectorAll<HTMLButtonElement>(
                                  '[role="menuitem"]',
                                ),
                              )
                              const index = items.indexOf(document.activeElement as HTMLButtonElement)
                              if (event.key === 'Escape') {
                                event.stopPropagation()
                                closeMenu(profile.id)
                              } else if (event.key === 'ArrowDown') {
                                event.preventDefault()
                                items[(index + 1) % items.length]?.focus()
                              } else if (event.key === 'ArrowUp') {
                                event.preventDefault()
                                items[(index - 1 + items.length) % items.length]?.focus()
                              }
                            }}
                          >
                            <MenuItem
                              icon={<Pencil size={13} />}
                              label="Edit"
                              autoFocus
                              onClick={() => {
                                setEditing(profile)
                                setFormOpen(true)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Copy size={13} />}
                              label="Duplicate"
                              onClick={() => {
                                clone(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Globe size={13} />}
                              label="Check proxy"
                              onClick={() => {
                                checkProxy(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<PackageOpen size={13} />}
                              label="Export…"
                              onClick={() => {
                                exportArchive(profile)
                                closeMenu(profile.id)
                              }}
                            />
                            <MenuItem
                              icon={<Trash2 size={13} />}
                              label="Delete"
                              danger
                              onClick={() => {
                                askDelete(profile)
                                closeMenu(profile.id)
                              }}
                            />
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between px-5 py-3 text-ink-dim">
          <span>
            {(currentPage - 1) * perPage + 1}–{Math.min(currentPage * perPage, visible.length)} of{' '}
            {visible.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              className="btn btn-ghost h-7 w-7 p-0"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
              aria-label="Previous page"
            >
              <ChevronLeft size={15} />
            </button>
            <span className="px-2 font-mono">
              {currentPage} / {totalPages}
            </span>
            <button
              className="btn btn-ghost h-7 w-7 p-0"
              disabled={currentPage === totalPages}
              onClick={() => setPage(currentPage + 1)}
              aria-label="Next page"
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>
      )}

      <ProfileForm
        open={formOpen}
        profile={editing}
        groups={groups}
        onClose={() => setFormOpen(false)}
        onSaved={loadProfiles}
      />

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ''}
        body={confirm?.body ?? ''}
        confirmLabel={confirm?.label}
        destructive={confirm?.label === 'Delete'}
        onConfirm={runConfirmed}
        onCancel={() => setConfirm(null)}
      />

      <Modal
        open={exportOpen}
        title="Export to Excel"
        onClose={() => setExportOpen(false)}
        width={440}
        footer={
          <>
            <button className="btn btn-default" onClick={() => setExportOpen(false)}>
              Cancel
            </button>
            <button className="btn btn-primary" onClick={exportExcel}>
              Export
            </button>
          </>
        }
      >
        <p className="text-ink-dim">
          The spreadsheet includes every profile setting so it can be imported back — including
          <span className="text-ink"> proxy passwords in clear text</span>. Store the file somewhere
          you would keep the passwords themselves.
        </p>
      </Modal>
    </>
  )
}

function StatusCell({ status, running }: { status: string; running: boolean }) {
  if (running) {
    return (
      <span className="inline-flex items-center gap-1.5 text-signal">
        <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-signal" />
        Running
      </span>
    )
  }
  const tone =
    status === 'blocked' ? 'bg-danger' : status === 'maintenance' ? 'bg-ink-dim' : 'bg-ink-faint'
  return (
    <span className="inline-flex items-center gap-1.5 capitalize text-ink-dim">
      <span className={`h-1.5 w-1.5 rounded-full ${tone}`} />
      {status}
    </span>
  )
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
  autoFocus,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  danger?: boolean
  autoFocus?: boolean
}) {
  return (
    <button
      role="menuitem"
      autoFocus={autoFocus}
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors hover:bg-line focus:bg-line ${
        danger ? 'text-danger' : 'text-ink'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}
