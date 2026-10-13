'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  accessAPI,
  type AccessRole,
  type AccessUser,
  type AuditEvent,
  type AuditFilters,
  type ProfileGrant,
} from '@/lib/access-api'
import { profilesAPI, type Profile } from '@/lib/api'
import { useAuth } from '@/components/login-gate'
import { useI18n } from '@/components/i18n-provider'
import { ConfirmDialog } from '@/components/modal'

async function allProfiles() {
  const profiles: Profile[] = []
  for (let page = 1; page <= 200; page++) {
    const response = await profilesAPI.getProfiles({ per_page: 100, page })
    profiles.push(...response.profiles)
    if (!response.has_next) return profiles
  }
  throw new Error('The profile collection is too large to load completely. No partial filter results are shown.')
}

const roles: AccessRole[] = ['administrator', 'operator', 'viewer']
const roleLabel = { administrator: 'Administrator', operator: 'Operator', viewer: 'Viewer' }
const filterLabel = { actor: 'Actor', action: 'Action', result: 'Result' }
const auditResults = ['success', 'denied', 'failure', 'partial', 'interrupted', 'skipped']

export default function AccessPage() {
  const { canManageAccess } = useAuth()
  const { t } = useI18n()
  const [users, setUsers] = useState<AccessUser[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [selected, setSelected] = useState('')
  const [grants, setGrants] = useState<ProfileGrant[]>([])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [filters, setFilters] = useState<AuditFilters>({ actor: '', action: '', result: '' })
  const [draftFilters, setDraftFilters] = useState(filters)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<AccessRole>('viewer')
  const [resetPassword, setResetPassword] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const newUserRole = users.length ? role : 'administrator'

  const load = useCallback(async () => {
    if (!canManageAccess) return
    const [userResponse, profiles] = await Promise.all([accessAPI.users(), allProfiles()])
    setUsers(userResponse.users)
    setProfiles(profiles)
  }, [canManageAccess])

  useEffect(() => {
    let alive = true
    if (canManageAccess) {
      // load only updates state after its awaited requests.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void load()
        .catch(error => { if (alive) setError(error.message) })
        .finally(() => { if (alive) setLoading(false) })
    }
    return () => { alive = false }
  }, [load, canManageAccess])

  useEffect(() => {
    let alive = true
    if (canManageAccess) {
      void accessAPI.audit(filters, offset).then(response => {
        if (alive) {
          setEvents(response.events)
          setTotal(response.total)
        }
      }).catch(error => {
        if (alive) {
          setEvents([])
          setError(error.message)
        }
      })
    }
    return () => { alive = false }
  }, [filters, offset, canManageAccess])

  useEffect(() => {
    let alive = true
    if (selected && canManageAccess) {
      void accessAPI.grants(selected).then(response => {
        if (alive) setGrants(response.grants)
      }).catch(error => {
        if (alive) {
          setGrants([])
          setError(error.message)
        }
      })
    }
    return () => { alive = false }
  }, [selected, canManageAccess])

  async function mutate(operation: () => Promise<unknown>) {
    setBusy(true)
    setError('')
    try {
      await operation()
      await load()
      const response = await accessAPI.audit(filters, offset)
      setEvents(response.events)
      setTotal(response.total)
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  function createUser(event: React.FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      await accessAPI.create(username, password, newUserRole)
      setUsername('')
      setPassword('')
    })
  }

  function changePassword(event: React.FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      await accessAPI.update(selected, { password: resetPassword })
      setResetPassword('')
    })
  }

  async function grant(profile: Profile, assigned: boolean, canExport: boolean) {
    await mutate(async () => {
      if (assigned) await accessAPI.grant(selected, profile.id, canExport)
      else await accessAPI.revoke(selected, profile.id)
      setGrants((await accessAPI.grants(selected)).grants)
    })
  }

  async function exportActivity() {
    await mutate(async () => {
      const response = await accessAPI.export(filters)
      // Keep the downloaded schema explicit even if the server adds new fields.
      const data = {
        total: response.total,
        events: response.events.map(({ id, actor_kind, actor_username, action, object_id, result, created_at }) =>
          ({ id, actor_kind, actor_username, action, object_id, result, created_at })),
      }
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'activity.json'
      link.click()
      URL.revokeObjectURL(url)
    })
  }

  function deleteUser() {
    if (!deleting) return
    void mutate(async () => {
      await accessAPI.remove(deleting)
      if (selected === deleting) {
        setSelected('')
        setGrants([])
      }
      setDeleting(null)
    })
  }

  if (!canManageAccess) return <p role="alert" className="p-6">{t('Access denied')}</p>

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-lg font-semibold">{t('Access and activity')}</h1>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {loading && <p role="status">{t('Loading…')}</p>}

      <section className="panel space-y-4 p-4" aria-labelledby="users-title">
        <h2 id="users-title" className="font-semibold">{t('Users')}</h2>
        <p className="text-ink-dim">
          {t('Operators can edit and launch assigned profiles. Viewers can inspect metadata. Export requires a separate grant.')}
        </p>
        <form className="flex flex-wrap items-end gap-3" onSubmit={createUser}>
          <label>
            {t('Username')}
            <input className="field block" value={username} onChange={event => setUsername(event.target.value)} autoComplete="off" required />
          </label>
          <label>
            {t('Password')}
            <input className="field block" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" minLength={8} required />
          </label>
          <div>
            <label htmlFor="access-new-user-role">{t('Role')}</label>
            <select id="access-new-user-role" className="field block" value={newUserRole} disabled={!users.length} onChange={event => setRole(event.target.value as AccessRole)}>
              {roles.map(role => <option key={role} value={role}>{t(roleLabel[role])}</option>)}
            </select>
          </div>
          <button className="btn btn-primary" disabled={busy || loading}>{t('Add user')}</button>
        </form>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr>{['Username', 'Role', 'Actions'].map(label => <th key={label} className="py-2">{t(label)}</th>)}</tr>
            </thead>
            <tbody>
              {users.map(user => (
                <tr key={user.username} className="border-t border-line">
                  <td className="py-2">{user.username}</td>
                  <td>
                    <select
                      className="field"
                      aria-label={t('Role for {user}', { user: user.username })}
                      value={user.role}
                      disabled={busy}
                      onChange={event => void mutate(() => accessAPI.update(user.username, { role: event.target.value as AccessRole }))}
                    >
                      {roles.map(role => <option key={role} value={role}>{t(roleLabel[role])}</option>)}
                    </select>
                  </td>
                  <td className="flex flex-wrap gap-2 py-2">
                    <button className="btn btn-default" disabled={busy} onClick={() => {
                      setSelected(user.username)
                      setGrants([])
                      setResetPassword('')
                    }}>
                      {t('Manage grants')}
                    </button>
                    <button className="btn btn-danger" disabled={busy} onClick={() => setDeleting(user.username)}>{t('Delete')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {selected && (
        <section className="panel space-y-4 p-4">
          <h2 className="font-semibold">{t('Profile grants for {user}', { user: selected })}</h2>
          <p className="text-ink-dim">{t('Export includes sensitive profile data. Grant it only when needed.')}</p>
          <form className="flex flex-wrap items-end gap-3" onSubmit={changePassword}>
            <label>
              {t('New password')}
              <input type="password" className="field block" autoComplete="new-password" minLength={8} required value={resetPassword} onChange={event => setResetPassword(event.target.value)} />
            </label>
            <button className="btn btn-default" disabled={busy || resetPassword.length < 8}>{t('Reset password')}</button>
          </form>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr>{['Profile', 'Assigned', 'Allow export'].map(label => <th className="py-2" key={label}>{t(label)}</th>)}</tr>
              </thead>
              <tbody>
                {profiles.map(profile => {
                  const assigned = grants.find(grant => grant.profile_id === profile.id)
                  return (
                    <tr key={profile.id} className="border-t border-line">
                      <td className="py-3">{profile.name}</td>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={t('Assign {profile}', { profile: profile.name })}
                          checked={!!assigned}
                          disabled={busy}
                          onChange={event => void grant(profile, event.target.checked, false)}
                        />
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={t('Allow export for {profile}', { profile: profile.name })}
                          checked={assigned?.can_export ?? false}
                          disabled={busy || !assigned}
                          onChange={event => void grant(profile, true, event.target.checked)}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="panel space-y-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="font-semibold">{t('Activity')}</h2>
          <button className="btn btn-default" disabled={busy} onClick={() => void exportActivity()}>{t('Export activity')}</button>
        </div>
        <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
          event.preventDefault()
          setOffset(0)
          setFilters(draftFilters)
        }}>
          {(['actor', 'action', 'result'] as const).map(key => (
            <div key={key}>
              <label htmlFor={`access-filter-${key}`}>{t(filterLabel[key])}</label>
              {key === 'result' ? (
                <select id="access-filter-result" className="field block" value={draftFilters.result} onChange={event => setDraftFilters({ ...draftFilters, result: event.target.value })}>
                  <option value="">{t('All results')}</option>
                  {auditResults.map(result => <option key={result} value={result}>{t(result)}</option>)}
                </select>
              ) : (
                <input id={`access-filter-${key}`} className="field block" value={draftFilters[key]} onChange={event => setDraftFilters({ ...draftFilters, [key]: event.target.value })} />
              )}
            </div>
          ))}
          <button className="btn btn-default">{t('Apply filters')}</button>
        </form>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr>{['Time', 'Actor', 'Action', 'Object', 'Result'].map(label => <th key={label} className="py-2">{t(label)}</th>)}</tr>
            </thead>
            <tbody>
              {events.map(event => (
                <tr key={event.id} className="border-t border-line">
                  <td className="py-2">{event.created_at}</td>
                  <td>{event.actor_username ?? event.actor_kind}</td>
                  <td>{event.action}</td>
                  <td>{event.object_id ?? '—'}</td>
                  <td>{event.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!events.length && <p className="py-4 text-ink-dim">{t('No activity found')}</p>}
        </div>
        <div className="flex items-center gap-3">
          <button className="btn btn-default" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>{t('Previous')}</button>
          <span>{t('{count} events', { count: total })}</span>
          <button className="btn btn-default" disabled={offset + 25 >= total} onClick={() => setOffset(offset + 25)}>{t('Next')}</button>
        </div>
      </section>

      <ConfirmDialog
        open={!!deleting}
        title={t('Delete user')}
        destructive
        body={t('Remove {user} and revoke their access?', { user: deleting ?? '' })}
        onCancel={() => setDeleting(null)}
        onConfirm={deleteUser}
      />
    </div>
  )
}
