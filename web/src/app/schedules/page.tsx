'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  CalendarClock,
  Pause,
  Pencil,
  Play,
  Plus,
  // lucide 1.0 renamed History to RotateCcwClock. The old name is the one that
  // means anything next to a History button, so keep it locally.
  RotateCcwClock as History,
  Trash2,
} from 'lucide-react'

import { useI18n } from '@/components/i18n-provider'
import type { Locale, Translator } from '@/lib/i18n'
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

function describeWhen(schedule: Schedule, t: Translator): string {
  if (schedule.kind === 'interval') {
    const minutes = schedule.interval_minutes ?? 0
    if (minutes % 1440 === 0) return t('every {count}d', { count: minutes / 1440 })
    if (minutes % 60 === 0) return t('every {count}h', { count: minutes / 60 })
    return t('every {count}m', { count: minutes })
  }
  const days =
    schedule.days && schedule.days.length > 0
      ? ` · ${schedule.days.map((day) => t(DAY_LABELS[day])).join(' ')}`
      : ''
  return t('daily at {time}{days}', { time: schedule.at_time, days })
}

function formatNextRun(value: string | null, locale: Locale): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(locale, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function SchedulesPage() {
  const { t, locale } = useI18n()
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
    // Clears the previous error synchronously before awaiting; see the note
    // on the profiles page.
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
      toast('error', t('Choose a profile'))
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
        toast('ok', t('Schedule updated'))
      } else {
        await schedulesAPI.create({ ...payload, profile_id: profileId })
        toast('ok', t('Schedule created'))
      }
      setFormOpen(false)
      load()
    } catch (err) {
      toast('error', editing ? t('Could not update schedule') : t('Could not create schedule'), String(err))
    } finally {
      setSaving(false)
    }
  }

  async function toggle(schedule: Schedule) {
    try {
      await schedulesAPI.update(schedule.id, { enabled: !schedule.enabled })
      toast('ok', schedule.enabled ? t('Schedule paused') : t('Schedule resumed'))
      load()
    } catch (err) {
      toast('error', t('Could not update schedule'), String(err))
    }
  }

  async function runNow(schedule: Schedule) {
    try {
      const run = await schedulesAPI.runNow(schedule.id)
      if (run.outcome === 'ok') toast('ok', t('Task ran'), run.message ?? undefined)
      else if (run.outcome === 'skipped') toast('ok', t('Task skipped'), run.message ?? undefined)
      else toast('error', t('Task failed'), run.message ?? undefined)
      load()
    } catch (err) {
      toast('error', t('Could not run the task'), String(err))
    }
  }

  async function remove() {
    if (!deleting) return
    const schedule = deleting
    setDeleting(null)
    try {
      await schedulesAPI.remove(schedule.id)
      toast('ok', t('Schedule deleted'))
      load()
    } catch (err) {
      toast('error', t('Could not delete schedule'), String(err))
    }
  }

  async function openHistory(schedule: Schedule) {
    setHistoryFor(schedule)
    setHistoryRuns([])
    try {
      const response = await schedulesAPI.runs(schedule.id)
      setHistoryRuns(response.runs)
    } catch (err) {
      toast('error', t('Could not load the history'), String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">{t('Schedules')}</h1>
        <span className="font-mono text-ink-faint">{schedules.length}</span>
        <button className="btn btn-primary ml-auto" onClick={openCreate}>
          <Plus size={14} strokeWidth={2.5} />
          {t('New schedule')}
        </button>
      </header>

      {error ? (
        <EmptyState
          icon={<CalendarClock size={18} />}
          title={t('Cannot reach the API')}
          body={error}
          action={
            <button className="btn btn-default" onClick={load}>
              {t('Retry')}
            </button>
          }
        />
      ) : loading ? (
        <p className="px-5 py-8 text-ink-faint">{t('Loading…')}</p>
      ) : schedules.length === 0 ? (
        <EmptyState
          icon={<CalendarClock size={18} />}
          title={t('Nothing scheduled')}
          body={t("Open a profile's browser on a schedule, or keep its pinned browser version current. Runs missed while the app is closed are skipped, not replayed.")}
          action={
            <button className="btn btn-primary" onClick={openCreate}>
              <Plus size={14} strokeWidth={2.5} />
              {t('Create a schedule')}
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="py-2 pl-5 pr-4 font-medium">{t('Profile')}
            </th>
              <th className="py-2 pr-4 font-medium">{t('Task')}
            </th>
              <th className="py-2 pr-4 font-medium">{t('When')}</th>
              <th className="py-2 pr-4 font-medium">{t('Next run')}</th>
              <th className="py-2 pr-4 font-medium">{t('Last run')}</th>
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
                  {schedule.profile_name ?? <span className="text-ink-faint">{t('deleted')}</span>}
                </td>
                <td className="py-2.5 pr-4 text-ink-dim">
                  {t(ACTION_LABELS[schedule.action])}
                  {schedule.run_minutes ? (
                    <span className="text-ink-faint"> · {t('{count}m session', { count: schedule.run_minutes })}</span>
                  ) : null}
                </td>
                <td className="py-2.5 pr-4 font-mono text-ink-dim">{describeWhen(schedule, t)}</td>
                <td className="py-2.5 pr-4 font-mono text-ink-dim">
                  {schedule.enabled ? formatNextRun(schedule.next_run_at, locale) : t('paused')}
                </td>
                <td className="py-2.5 pr-4">
                  {schedule.last_run ? (
                    <button
                      className={`font-mono ${OUTCOME_STYLES[schedule.last_run.outcome]} hover:underline`}
                      title={schedule.last_run.message ?? undefined}
                      onClick={() => openHistory(schedule)}
                    >
                      {t(schedule.last_run.outcome)} · {formatLastUsed(schedule.last_run.started_at, locale)}
                    </button>
                  ) : (
                    <span className="text-ink-faint">—</span>
                  )}
                </td>
                <td className="py-2.5 pr-5">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={t('Run {name} now', { name: schedule.profile_name ?? schedule.id })}
                      title={t('Run now')}
                      onClick={() => runNow(schedule)}
                    >
                      <Play size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={schedule.enabled ? t('Pause schedule') : t('Resume schedule')}
                      title={schedule.enabled ? t('Pause') : t('Resume')}
                      onClick={() => toggle(schedule)}
                    >
                      {schedule.enabled ? <Pause size={13} /> : <Play size={13} className="text-signal" />}
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={t('Run history')}
                      title={t('History')}
                      onClick={() => openHistory(schedule)}
                    >
                      <History size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={t('Edit schedule')}
                      title={t('Edit')}
                      onClick={() => openEdit(schedule)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0 hover:text-danger"
                      aria-label={t('Delete schedule')}
                      title={t('Delete')}
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
        title={editing ? t('Edit schedule') : t('New schedule')}
        subtitle={t("Times are read on the server's clock — the machine running camoufox-pm.")}
        onClose={() => setFormOpen(false)}
        width={480}
        footer={
          <>
            <button className="btn btn-default" onClick={() => setFormOpen(false)}>
              {t('Cancel')}
            </button>
            <button type="submit" form="schedule-form" className="btn btn-primary" disabled={saving}>
              {editing ? t('Save changes') : t('Create schedule')}
            </button>
          </>
        }
      >
        <form id="schedule-form" onSubmit={save} className="flex flex-col gap-3">
          <div>
            <label className="field-label" htmlFor="schedule-profile">
              {t('Profile')}
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
                {t('Choose a profile')}
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
              {t('Task')}
            </label>
            <select
              id="schedule-action"
              className="field"
              value={action}
              onChange={(event) => setAction(event.target.value as ScheduleAction)}
            >
              <option value="launch">{t('Open browser — warm the profile with a session')}</option>
              <option value="refresh_browser">
                {t("Refresh browser version — keep the pinned machine's browser current")}
              </option>
            </select>
            <p className="mt-1 text-ink-faint">
              {action === 'refresh_browser'
                ? t('Moves only the browser version onto the installed one; the screen, GPU, cores and seeds stay. Regenerating the hardware itself is deliberately not schedulable — it would make the profile a new machine on a timer.')
                : t('Launches through the same session manager as the Open button; if the browser is already running, the run is skipped.')}
            </p>
          </div>

          <div>
            <span className="field-label">{t('Repeats')}</span>
            <div className="flex gap-2">
              <button
                type="button"
                className={kind === 'daily' ? 'btn btn-default border-signal text-ink' : 'btn btn-default'}
                aria-pressed={kind === 'daily'}
                onClick={() => setKind('daily')}
              >
                {t('Daily at a time')}
              </button>
              <button
                type="button"
                className={kind === 'interval' ? 'btn btn-default border-signal text-ink' : 'btn btn-default'}
                aria-pressed={kind === 'interval'}
                onClick={() => setKind('interval')}
              >
                {t('Every N minutes')}
              </button>
            </div>
          </div>

          {kind === 'interval' ? (
            <div>
              <label className="field-label" htmlFor="schedule-interval">
                {t('Every (minutes)')}
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
                  {t('At (server time)')}
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
                <span className="field-label">{t('On days (none = every day)')}</span>
                <div className="flex gap-1">
                  {DAY_LABELS.map((label, day) => (
                    <button
                      key={t(label)}
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
                      {t(label)}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}

          {action === 'launch' && (
            <div>
              <label className="field-label" htmlFor="schedule-run-minutes">
                {t('Close after (minutes, empty = leave open)')}
              </label>
              <input
                id="schedule-run-minutes"
                type="number"
                min={1}
                max={1440}
                className="field font-mono"
                value={runMinutes}
                onChange={(event) => setRunMinutes(event.target.value)}
                placeholder={t('Leave the browser open')}
              />
            </div>
          )}
        </form>
      </Modal>

      <Modal
        open={historyFor !== null}
        title={t('Run history')}
        subtitle={
          historyFor
            ? t('{name} · {action} · newest first, last 20 kept', { name: historyFor.profile_name ?? historyFor.profile_id, action: t(ACTION_LABELS[historyFor.action]) })
            : undefined
        }
        onClose={() => setHistoryFor(null)}
        width={520}
      >
        {historyRuns.length === 0 ? (
          <p className="text-ink-faint">{t('No runs recorded yet.')}</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line">
            {historyRuns.map((run) => (
              <li key={run.id} className="flex items-baseline gap-3 py-2">
                <span className={`w-[64px] shrink-0 font-mono ${OUTCOME_STYLES[run.outcome]}`}>
                  {t(run.outcome)}
                </span>
                <span className="w-[128px] shrink-0 font-mono text-ink-dim">
                  {formatNextRun(run.started_at, locale)}
                </span>
                <span className="text-ink-dim">{run.message ?? '—'}</span>
              </li>
            ))}
          </ul>
        )}
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title={t('Delete schedule')}
        body={
          deleting
            ? t('The {action} schedule for "{name}" and its run history will be removed. The profile itself is not touched.', { action: t(ACTION_LABELS[deleting.action]).toLowerCase(), name: deleting.profile_name ?? deleting.profile_id })
            : ''
        }
        confirmLabel={t('Delete')}
        destructive
        onConfirm={remove}
        onCancel={() => setDeleting(null)}
      />
    </>
  )
}
