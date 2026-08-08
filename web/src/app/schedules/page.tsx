'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  CalendarClock,
  History,
  Pause,
  Pencil,
  Play,
  Plus,
  Trash2,
} from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { ConfirmDialog, Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import {
  formatLastUsed,
  profilesAPI,
  schedulesAPI,
  type Profile,
  type Schedule,
  type ScheduleAction,
  type ScheduleKind,
  type ScheduleRun,
  type ScheduleRunOutcome,
} from '@/lib/api'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const ACTION_LABELS: Record<ScheduleAction, string> = {
  launch: 'Open browser',
  refresh_browser: 'Refresh browser version',
}

const OUTCOME_STYLES: Record<ScheduleRunOutcome, string> = {
  ok: 'text-ok',
  skipped: 'text-ink-dim',
  error: 'text-danger',
  missed: 'text-warn',
}

function describeWhen(schedule: Schedule): string {
  if (schedule.kind === 'interval') {
    const minutes = schedule.interval_minutes ?? 0
    if (minutes % 1440 === 0) return `every ${minutes / 1440}d`
    if (minutes % 60 === 0) return `every ${minutes / 60}h`
    return `every ${minutes}m`
  }
  const days =
    schedule.days && schedule.days.length > 0
      ? ` · ${schedule.days.map((day) => DAY_LABELS[day]).join(' ')}`
      : ''
  return `daily at ${schedule.at_time}${days}`
}

function formatNextRun(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<Schedule | null>(null)
  const [historyFor, setHistoryFor] = useState<Schedule | null>(null)
  const [historyRuns, setHistoryRuns] = useState<ScheduleRun[]>([])

  // Form fields
  const [profileId, setProfileId] = useState('')
  const [action, setAction] = useState<ScheduleAction>('launch')
  const [kind, setKind] = useState<ScheduleKind>('daily')
  const [intervalMinutes, setIntervalMinutes] = useState('60')
  const [atTime, setAtTime] = useState('09:00')
  const [days, setDays] = useState<number[]>([])
  const [runMinutes, setRunMinutes] = useState('')

  const toast = useToast()

  const load = useCallback(async () => {
    try {
      setError(null)
      const [scheduleList, profileList] = await Promise.all([
        schedulesAPI.list(),
        profilesAPI.getProfiles({ per_page: 100 }),
      ])
      setSchedules(scheduleList.schedules)
      setProfiles(profileList.profiles)
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  function openCreate() {
    setEditing(null)
    setProfileId(profiles[0]?.id ?? '')
    setAction('launch')
    setKind('daily')
    setIntervalMinutes('60')
    setAtTime('09:00')
    setDays([])
    setRunMinutes('')
    setFormOpen(true)
  }

  function openEdit(schedule: Schedule) {
    setEditing(schedule)
    setProfileId(schedule.profile_id)
    setAction(schedule.action)
    setKind(schedule.kind)
    setIntervalMinutes(String(schedule.interval_minutes ?? 60))
    setAtTime(schedule.at_time ?? '09:00')
    setDays(schedule.days ?? [])
    setRunMinutes(schedule.run_minutes ? String(schedule.run_minutes) : '')
    setFormOpen(true)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!profileId) {
      toast('error', 'Choose a profile')
      return
    }
    const payload = {
      action,
      kind,
      interval_minutes: kind === 'interval' ? Number(intervalMinutes) || 60 : null,
      at_time: kind === 'daily' ? atTime : null,
      days: kind === 'daily' && days.length > 0 ? days : null,
      run_minutes: action === 'launch' && runMinutes ? Number(runMinutes) : null,
    }
    setSaving(true)
    try {
      if (editing) {
        await schedulesAPI.update(editing.id, payload)
        toast('ok', 'Schedule updated')
      } else {
        await schedulesAPI.create({ ...payload, profile_id: profileId })
        toast('ok', 'Schedule created')
      }
      setFormOpen(false)
      load()
    } catch (err) {
      toast('error', editing ? 'Could not update schedule' : 'Could not create schedule', String(err))
    } finally {
      setSaving(false)
    }
  }

  async function toggle(schedule: Schedule) {
    try {
      await schedulesAPI.update(schedule.id, { enabled: !schedule.enabled })
      toast('ok', schedule.enabled ? 'Schedule paused' : 'Schedule resumed')
      load()
    } catch (err) {
      toast('error', 'Could not update schedule', String(err))
    }
  }

  async function runNow(schedule: Schedule) {
    try {
      const run = await schedulesAPI.runNow(schedule.id)
      if (run.outcome === 'ok') toast('ok', 'Task ran', run.message ?? undefined)
      else if (run.outcome === 'skipped') toast('ok', 'Task skipped', run.message ?? undefined)
      else toast('error', 'Task failed', run.message ?? undefined)
      load()
    } catch (err) {
      toast('error', 'Could not run the task', String(err))
    }
  }

  async function remove() {
    if (!deleting) return
    const schedule = deleting
    setDeleting(null)
    try {
      await schedulesAPI.remove(schedule.id)
      toast('ok', 'Schedule deleted')
      load()
    } catch (err) {
      toast('error', 'Could not delete schedule', String(err))
    }
  }

  async function openHistory(schedule: Schedule) {
    setHistoryFor(schedule)
    setHistoryRuns([])
    try {
      const response = await schedulesAPI.runs(schedule.id)
      setHistoryRuns(response.runs)
    } catch (err) {
      toast('error', 'Could not load the history', String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">Schedules</h1>
        <span className="font-mono text-ink-faint">{schedules.length}</span>
        <button className="btn btn-primary ml-auto" onClick={openCreate}>
          <Plus size={14} strokeWidth={2.5} />
          New schedule
        </button>
      </header>

      {error ? (
        <EmptyState
          icon={<CalendarClock size={18} />}
          title="Cannot reach the API"
          body={error}
          action={
            <button className="btn btn-default" onClick={load}>
              Retry
            </button>
          }
        />
      ) : loading ? (
        <p className="px-5 py-8 text-ink-faint">Loading…</p>
      ) : schedules.length === 0 ? (
        <EmptyState
          icon={<CalendarClock size={18} />}
          title="Nothing scheduled"
          body="Open a profile's browser on a schedule, or keep its pinned browser version current. Runs missed while the app is closed are skipped, not replayed."
          action={
            <button className="btn btn-primary" onClick={openCreate}>
              <Plus size={14} strokeWidth={2.5} />
              Create a schedule
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="py-2 pl-5 pr-4 font-medium">Profile</th>
              <th className="py-2 pr-4 font-medium">Task</th>
              <th className="py-2 pr-4 font-medium">When</th>
              <th className="py-2 pr-4 font-medium">Next run</th>
              <th className="py-2 pr-4 font-medium">Last run</th>
              <th className="w-[168px] py-2 pr-5" />
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule, index) => (
              <tr
                key={schedule.id}
                className={`row-in border-b border-line/60 hover:bg-surface ${
                  schedule.enabled ? '' : 'opacity-50'
                }`}
                style={{ animationDelay: `${Math.min(index, 12) * 12}ms` }}
              >
                <td className="py-2.5 pl-5 pr-4 font-medium">
                  {schedule.profile_name ?? <span className="text-ink-faint">deleted</span>}
                </td>
                <td className="py-2.5 pr-4 text-ink-dim">
                  {ACTION_LABELS[schedule.action]}
                  {schedule.run_minutes ? (
                    <span className="text-ink-faint"> · {schedule.run_minutes}m session</span>
                  ) : null}
                </td>
                <td className="py-2.5 pr-4 font-mono text-ink-dim">{describeWhen(schedule)}</td>
                <td className="py-2.5 pr-4 font-mono text-ink-dim">
                  {schedule.enabled ? formatNextRun(schedule.next_run_at) : 'paused'}
                </td>
                <td className="py-2.5 pr-4">
                  {schedule.last_run ? (
                    <button
                      className={`font-mono ${OUTCOME_STYLES[schedule.last_run.outcome]} hover:underline`}
                      title={schedule.last_run.message ?? undefined}
                      onClick={() => openHistory(schedule)}
                    >
                      {schedule.last_run.outcome} · {formatLastUsed(schedule.last_run.started_at)}
                    </button>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </td>
                <td className="py-2.5 pr-5">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={`Run ${schedule.profile_name ?? schedule.id} now`}
                      title="Run now"
                      onClick={() => runNow(schedule)}
                    >
                      <Play size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={schedule.enabled ? 'Pause schedule' : 'Resume schedule'}
                      title={schedule.enabled ? 'Pause' : 'Resume'}
                      onClick={() => toggle(schedule)}
                    >
                      {schedule.enabled ? <Pause size={13} /> : <Play size={13} className="text-signal" />}
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label="Run history"
                      title="History"
                      onClick={() => openHistory(schedule)}
                    >
                      <History size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label="Edit schedule"
                      title="Edit"
                      onClick={() => openEdit(schedule)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0 hover:text-danger"
                      aria-label="Delete schedule"
                      title="Delete"
                      onClick={() => setDeleting(schedule)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Modal
        open={formOpen}
        title={editing ? 'Edit schedule' : 'New schedule'}
        subtitle="Times are read on the server's clock — the machine running camoufox-pm."
        onClose={() => setFormOpen(false)}
        width={480}
        footer={
          <>
            <button className="btn btn-default" onClick={() => setFormOpen(false)}>
              Cancel
            </button>
            <button type="submit" form="schedule-form" className="btn btn-primary" disabled={saving}>
              {editing ? 'Save changes' : 'Create schedule'}
            </button>
          </>
        }
      >
        <form id="schedule-form" onSubmit={save} className="flex flex-col gap-3">
          <div>
            <label className="field-label" htmlFor="schedule-profile">
              Profile
            </label>
            <select
              id="schedule-profile"
              className="field"
              value={profileId}
              onChange={(event) => setProfileId(event.target.value)}
              disabled={editing !== null}
              required
            >
              <option value="" disabled>
                Choose a profile
              </option>
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="field-label" htmlFor="schedule-action">
              Task
            </label>
            <select
              id="schedule-action"
              className="field"
              value={action}
              onChange={(event) => setAction(event.target.value as ScheduleAction)}
            >
              <option value="launch">Open browser — warm the profile with a session</option>
              <option value="refresh_browser">
                Refresh browser version — keep the pinned machine&apos;s browser current
              </option>
            </select>
            <p className="mt-1 text-ink-faint">
              {action === 'refresh_browser'
                ? 'Moves only the browser version onto the installed one; the screen, GPU, cores and seeds stay. Regenerating the hardware itself is deliberately not schedulable — it would make the profile a new machine on a timer.'
                : 'Launches through the same session manager as the Open button; if the browser is already running, the run is skipped.'}
            </p>
          </div>

          <div>
            <span className="field-label">Repeats</span>
            <div className="flex gap-2">
              <button
                type="button"
                className={kind === 'daily' ? 'btn btn-default border-signal text-ink' : 'btn btn-default'}
                aria-pressed={kind === 'daily'}
                onClick={() => setKind('daily')}
              >
                Daily at a time
              </button>
              <button
                type="button"
                className={kind === 'interval' ? 'btn btn-default border-signal text-ink' : 'btn btn-default'}
                aria-pressed={kind === 'interval'}
                onClick={() => setKind('interval')}
              >
                Every N minutes
              </button>
            </div>
          </div>

          {kind === 'interval' ? (
            <div>
              <label className="field-label" htmlFor="schedule-interval">
                Every (minutes)
              </label>
              <input
                id="schedule-interval"
                type="number"
                min={1}
                max={40320}
                className="field font-mono"
                value={intervalMinutes}
                onChange={(event) => setIntervalMinutes(event.target.value)}
                required
              />
            </div>
          ) : (
            <>
              <div>
                <label className="field-label" htmlFor="schedule-time">
                  At (server time)
                </label>
                <input
                  id="schedule-time"
                  type="time"
                  className="field font-mono"
                  value={atTime}
                  onChange={(event) => setAtTime(event.target.value)}
                  required
                />
              </div>
              <div>
                <span className="field-label">On days (none = every day)</span>
                <div className="flex gap-1">
                  {DAY_LABELS.map((label, day) => (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={days.includes(day)}
                      className={`btn h-7 px-2 font-mono ${
                        days.includes(day) ? 'btn-default border-signal text-ink' : 'btn-ghost'
                      }`}
                      onClick={() =>
                        setDays((current) =>
                          current.includes(day)
                            ? current.filter((d) => d !== day)
                            : [...current, day].sort(),
                        )
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {action === 'launch' && (
            <div>
              <label className="field-label" htmlFor="schedule-run-minutes">
                Close after (minutes, empty = leave open)
              </label>
              <input
                id="schedule-run-minutes"
                type="number"
                min={1}
                max={1440}
                className="field font-mono"
                value={runMinutes}
                onChange={(event) => setRunMinutes(event.target.value)}
                placeholder="Leave the browser open"
              />
            </div>
          )}
        </form>
      </Modal>

      <Modal
        open={historyFor !== null}
        title="Run history"
        subtitle={
          historyFor
            ? `${historyFor.profile_name ?? historyFor.profile_id} · ${ACTION_LABELS[historyFor.action]} · newest first, last 20 kept`
            : undefined
        }
        onClose={() => setHistoryFor(null)}
        width={520}
      >
        {historyRuns.length === 0 ? (
          <p className="text-ink-faint">No runs recorded yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {historyRuns.map((run) => (
              <li key={run.id} className="flex items-baseline gap-3 py-2">
                <span className={`w-[64px] shrink-0 font-mono ${OUTCOME_STYLES[run.outcome]}`}>
                  {run.outcome}
                </span>
                <span className="w-[128px] shrink-0 font-mono text-ink-dim">
                  {formatNextRun(run.started_at)}
                </span>
                <span className="text-ink-dim">{run.message ?? '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title="Delete schedule"
        body={
          deleting
            ? `The ${ACTION_LABELS[deleting.action].toLowerCase()} schedule for "${
                deleting.profile_name ?? deleting.profile_id
              }" and its run history will be removed. The profile itself is not touched.`
            : ''
        }
        confirmLabel="Delete"
        destructive
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      />
    </>
  )
}
