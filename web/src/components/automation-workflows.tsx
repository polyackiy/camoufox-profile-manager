'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { useAuth } from '@/components/login-gate'
import { Modal, ConfirmDialog } from '@/components/modal'
import {
  profilesAPI,
  workflowsAPI,
  type Profile,
  type Workflow,
  type WorkflowDefinition,
  type WorkflowRun,
  type WorkflowStep,
} from '@/lib/api'

const initial = (): WorkflowDefinition => ({
  name: '',
  steps: [
    { name: 'Start', action: 'launch' },
    { name: 'Finish', action: 'close' },
  ],
  concurrency: 1,
  timing: null,
  schedule_enabled: false,
  scheduled_targets: [],
})
const states: Record<string, string> = {
  queued: 'Queued',
  running: 'In progress',
  completed: 'Completed',
  failed: 'Failed',
  partial: 'Partial',
  cancelled: 'Cancelled',
  interrupted: 'Interrupted',
  missed: 'Missed',
  skipped: 'Skipped',
  denied: 'Denied',
}
const actions = {
  launch: 'Open browser',
  navigate: 'Navigate to URL',
  wait: 'Wait',
  close: 'Close browser',
}
const active = (run: WorkflowRun) => ['queued', 'running'].includes(run.state)

/** Declarative workflows share the existing schedule workspace and dialog shell. */
export function AutomationWorkflows() {
  const { t, locale } = useI18n()
  const { role } = useAuth()
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<Workflow | 'new' | null>(null)
  const [draft, setDraft] = useState<WorkflowDefinition>(initial)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState('')
  const [profilesReady, setProfilesReady] = useState(false)
  const [review, setReview] = useState<{
    workflow: Workflow
    requestId: string
    retry?: WorkflowRun
  } | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [history, setHistory] = useState<Workflow | null>(null)
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [runId, setRunId] = useState('')
  const [deleting, setDeleting] = useState<Workflow | null>(null)
  const [cancelError, setCancelError] = useState(false)

  const fail = (error: unknown) =>
    setError(
      error instanceof Error
        ? error.message
        : 'Workflow request failed. Refresh and try again.',
    )
  const load = useCallback(async () => {
    try {
      setWorkflows((await workflowsAPI.list()).workflows)
      setError(null)
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : 'Workflow request failed. Refresh and try again.',
      )
    }
  }, [])
  useEffect(() => {
    if (role === 'viewer') return
    const timer = setTimeout(() => {
      void load()
    }, 0)
    return () => clearTimeout(timer)
  }, [load, role])
  const refreshRuns = useCallback(async () => {
    if (!history) return
    try {
      setRuns((await workflowsAPI.runs(history.id)).runs)
      setCancelError(false)
    } catch {
      setCancelError(true)
    }
  }, [history])
  useEffect(() => {
    if (!history) return
    const timer = setTimeout(() => {
      void refreshRuns()
    }, 0)
    return () => clearTimeout(timer)
  }, [history, refreshRuns])
  useEffect(() => {
    if (!history || !runs.some(active)) return
    const timer = setInterval(() => {
      void refreshRuns()
    }, 2000)
    return () => clearInterval(timer)
  }, [history, runs, refreshRuns])

  async function loadProfiles() {
    setProfilesReady(false)
    try {
      const result: Profile[] = []
      for (let page = 1; page <= 100; page++) {
        const response = await profilesAPI.getProfiles({ page, per_page: 100 })
        result.push(...response.profiles)
        if (!response.has_next) {
          setProfiles(result)
          setProfilesReady(true)
          return
        }
      }
      throw new Error(
        'Too many profiles to review. Use the workflow API with explicit profile IDs.',
      )
    } catch (error) {
      fail(error)
    }
  }

  function openEditor(value: Workflow | 'new') {
    setError(null)
    setEditing(value)
    setSearch('')
    setDraft(
      value === 'new'
        ? {
            ...initial(),
            steps: [
              { name: t('Open browser'), action: 'launch' },
              { name: t('Close browser'), action: 'close' },
            ],
          }
        : structuredClone(value.definition),
    )
    setSelected(
      new Set(
        value === 'new'
          ? []
          : value.definition.scheduled_targets.map(
              (target) => target.profile_id,
            ),
      ),
    )
    void loadProfiles()
  }
  function openReview(workflow: Workflow, retry?: WorkflowRun) {
    setHistory(null)
    setError(null)
    setSearch('')
    setAcknowledged(false)
    setSelected(
      new Set(
        retry?.targets
          .filter((target) => target.state !== 'completed')
          .map((target) => target.profile_id) ?? [],
      ),
    )
    setReview({ workflow, requestId: crypto.randomUUID(), retry })
    void loadProfiles()
  }
  const targets = profiles
    .filter((profile) => selected.has(profile.id))
    .map((profile) => ({
      profile_id: profile.id,
      row_version: profile.row_version,
    }))
  const validTargets =
    profilesReady &&
    selected.size > 0 &&
    selected.size <= 100 &&
    targets.length === selected.size
  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!editing || (draft.schedule_enabled && !validTargets)) return
    setBusy(true)
    setError(null)
    const body = {
      ...draft,
      scheduled_targets: draft.schedule_enabled ? targets : [],
    }
    try {
      if (editing === 'new') await workflowsAPI.create(body)
      else
        await workflowsAPI.update(editing.id, {
          ...body,
          revision: editing.revision,
        })
      setEditing(null)
      await load()
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }
  async function start() {
    if (!review || !validTargets || (review.retry && !acknowledged)) return
    setBusy(true)
    setError(null)
    try {
      const run = await workflowsAPI.start(review.workflow.id, {
        request_id: review.requestId,
        revision: review.workflow.revision,
        targets,
        ...(review.retry
          ? { retry_of: review.retry.id, replay_confirmed: acknowledged }
          : {}),
      })
      setRuns([run])
      setRunId(run.id)
      setHistory(review.workflow)
      setReview(null)
    } catch (error) {
      fail(error)
    } finally {
      setBusy(false)
    }
  }
  function updateStep(index: number, change: Partial<WorkflowStep>) {
    setDraft((current) => ({
      ...current,
      steps: current.steps.map((step, position) =>
        position === index ? { ...step, ...change } : step,
      ),
    }))
  }
  function move(index: number, offset: number) {
    setDraft((current) => {
      const steps = [...current.steps]
      ;[steps[index], steps[index + offset]] = [
        steps[index + offset],
        steps[index],
      ]
      return { ...current, steps }
    })
  }
  const currentRun = runs.find((run) => run.id === runId) ?? runs[0]
  const eligible = review?.retry
    ? new Set(
        review.retry.targets
          .filter((target) => target.state !== 'completed')
          .map((target) => target.profile_id),
      )
    : null
  const picker = (
    <fieldset className="mt-3 space-y-2">
      <legend className="font-medium">{t('Workflow targets')}</legend>
      <button
        type="button"
        className="btn btn-ghost"
        disabled={busy || selected.size === 0}
        onClick={() => setSelected(new Set())}
      >
        {t('Clear selection')}
      </button>
      <p className="text-ink-dim">
        {t(
          'Select up to 100 profiles. Open, busy or changed profiles are skipped or refused; existing browsers are never taken over.',
        )}
      </p>
      <label htmlFor="workflow-search" className="field-label">
        {t('Search profiles')}
      </label>
      <input
        id="workflow-search"
        className="field"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {!profilesReady ? (
        <p role="status">{t('Loading…')}</p>
      ) : (
        <div className="max-h-48 overflow-y-auto rounded border border-line p-2">
          {profiles
            .filter(
              (profile) =>
                (!eligible || eligible.has(profile.id)) &&
                profile.name.toLowerCase().includes(search.toLowerCase()),
            )
            .map((profile) => (
              <label
                key={profile.id}
                className="flex min-h-9 items-center gap-2 break-words"
              >
                <input
                  type="checkbox"
                  checked={selected.has(profile.id)}
                  disabled={busy}
                  onChange={(event) =>
                    setSelected((current) => {
                      const next = new Set(current)
                      if (event.target.checked) next.add(profile.id)
                      else next.delete(profile.id)
                      return next
                    })
                  }
                />
                {profile.name}
              </label>
            ))}
        </div>
      )}
      <p role="status">{t('Selected: {count}', { count: selected.size })}</p>
      {selected.size > 0 && (
        <ul
          aria-label={t('Selected profiles')}
          className="space-y-1 text-ink-dim break-words"
        >
          {profiles
            .filter((profile) => selected.has(profile.id))
            .map((profile) => (
              <li key={profile.id}>
                {profile.name} · {profile.id}
              </li>
            ))}
        </ul>
      )}
      {profilesReady && selected.size > 0 && !validTargets && (
        <p className="text-danger" role="alert">
          {t(
            'Review the selection: at most 100 available profiles are allowed.',
          )}
        </p>
      )}
    </fieldset>
  )

  if (role === 'viewer') return null
  return (
    <section className="m-5 panel p-4" aria-labelledby="automation-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="automation-heading" className="font-semibold">
          {t('Automation workflows')}
        </h2>
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-ghost" onClick={load}>
            {t('Refresh')}
          </button>
          <button className="btn btn-primary" onClick={() => openEditor('new')}>
            <Plus size={14} aria-hidden="true" />
            {t('New workflow')}
          </button>
        </div>
      </div>
      <p className="mt-2 text-ink-dim">
        {t(
          'Build a sequence of browser actions, review its targets and follow each step. Missed runs never replay automatically.',
        )}
      </p>
      {error && !editing && !review && (
        <p role="alert" className="mt-2 break-words text-danger">
          {t(error)}
        </p>
      )}
      {!workflows.length && (
        <p className="mt-3 text-ink-faint">{t('No workflows yet.')}</p>
      )}
      <ul className="mt-3 divide-y divide-line">
        {workflows.map((workflow) => (
          <li
            key={workflow.id}
            className="flex flex-wrap items-center justify-between gap-3 py-3"
          >
            <div className="min-w-0 break-words">
              <h3 className="font-medium">{workflow.definition.name}</h3>
              <p className="text-ink-dim">
                {workflow.definition.schedule_enabled
                  ? t('Scheduled workflow')
                  : t('Manual workflow')}
                {workflow.next_run_at
                  ? ` · ${new Date(workflow.next_run_at).toLocaleString(locale)}`
                  : ''}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                className="btn btn-primary"
                onClick={() => openReview(workflow)}
              >
                {t('Review and run')}
              </button>
              <button
                className="btn btn-default"
                onClick={() => {
                  setHistory(workflow)
                  setRuns([])
                  setRunId('')
                  setError(null)
                  void loadProfiles()
                }}
              >
                {t('Run history')}
              </button>
              <button
                className="btn btn-ghost"
                onClick={() => openEditor(workflow)}
              >
                {t('Edit')}
              </button>
              <button
                className="btn btn-ghost"
                aria-label={t('Delete workflow {name}', {
                  name: workflow.definition.name,
                })}
                onClick={() => setDeleting(workflow)}
              >
                <Trash2 size={14} aria-hidden="true" />
              </button>
            </div>
          </li>
        ))}
      </ul>
      <Modal
        open={!!editing}
        title={editing === 'new' ? t('New workflow') : t('Edit workflow')}
        onClose={() => {
          if (!busy) setEditing(null)
        }}
        footer={
          <>
            <button
              className="btn btn-default"
              disabled={busy}
              onClick={() => setEditing(null)}
            >
              {t('Cancel')}
            </button>
            <button
              className="btn btn-primary"
              form="workflow-form"
              disabled={busy || (draft.schedule_enabled && !validTargets)}
            >
              {t('Save workflow')}
            </button>
          </>
        }
      >
        <form id="workflow-form" onSubmit={save} className="space-y-4">
          <div>
            <label htmlFor="workflow-name" className="field-label">
              {t('Workflow name')}
            </label>
            <input
              id="workflow-name"
              className="field"
              required
              maxLength={80}
              value={draft.name}
              data-modal-autofocus
              onChange={(event) =>
                setDraft({ ...draft, name: event.target.value })
              }
            />
          </div>
          <p className="text-ink-dim">
            {t(
              'Every workflow starts with a new browser session and ends by closing it. Scripts, cookies and passwords are not recorded.',
            )}
          </p>
          <ol className="space-y-3">
            {draft.steps.map((step, index) => (
              <li
                key={index}
                className="rounded border border-line p-3 space-y-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-ink-dim">
                    {index + 1}. {t(actions[step.action])}
                  </span>
                  {index > 0 && index < draft.steps.length - 1 && (
                    <div className="ml-auto flex gap-1">
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={index <= 1}
                        aria-label={t('Move step up')}
                        onClick={() => move(index, -1)}
                      >
                        <ArrowUp size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={index >= draft.steps.length - 2}
                        aria-label={t('Move step down')}
                        onClick={() => move(index, 1)}
                      >
                        <ArrowDown size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        aria-label={t('Remove step')}
                        onClick={() =>
                          setDraft({
                            ...draft,
                            steps: draft.steps.filter(
                              (_, position) => position !== index,
                            ),
                          })
                        }
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </div>
                  )}
                </div>
                <label
                  htmlFor={`workflow-step-${index}`}
                  className="field-label"
                >
                  {t('Step name')}
                </label>
                <input
                  id={`workflow-step-${index}`}
                  className="field"
                  value={step.name}
                  maxLength={80}
                  required
                  onChange={(event) =>
                    updateStep(index, { name: event.target.value })
                  }
                />
                {step.action === 'navigate' && (
                  <>
                    <label
                      className="field-label"
                      htmlFor={`workflow-url-${index}`}
                    >
                      {t('Destination URL')}
                    </label>
                    <input
                      id={`workflow-url-${index}`}
                      className="field font-mono"
                      type="url"
                      required
                      maxLength={2048}
                      value={step.url ?? ''}
                      placeholder="https://example.com"
                      onChange={(event) =>
                        updateStep(index, { url: event.target.value })
                      }
                    />
                  </>
                )}
                {step.action === 'wait' && (
                  <>
                    <label
                      className="field-label"
                      htmlFor={`workflow-wait-${index}`}
                    >
                      {t('Wait seconds')}
                    </label>
                    <input
                      id={`workflow-wait-${index}`}
                      className="field"
                      type="number"
                      min={1}
                      max={60}
                      required
                      value={step.seconds ?? 1}
                      onChange={(event) =>
                        updateStep(index, {
                          seconds: Number(event.target.value),
                        })
                      }
                    />
                  </>
                )}
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-default"
              disabled={draft.steps.length >= 12}
              onClick={() =>
                setDraft({
                  ...draft,
                  steps: [
                    ...draft.steps.slice(0, -1),
                    { name: t('Navigate to URL'), action: 'navigate', url: '' },
                    draft.steps.at(-1)!,
                  ],
                })
              }
            >
              {t('Add navigation')}
            </button>
            <button
              type="button"
              className="btn btn-default"
              disabled={draft.steps.length >= 12}
              onClick={() =>
                setDraft({
                  ...draft,
                  steps: [
                    ...draft.steps.slice(0, -1),
                    { name: t('Wait'), action: 'wait', seconds: 5 },
                    draft.steps.at(-1)!,
                  ],
                })
              }
            >
              {t('Add wait')}
            </button>
          </div>
          <p className="text-ink-dim">
            {t(
              'Use HTTP or HTTPS destinations without embedded credentials. URLs are saved in the workflow definition; do not put passwords or tokens in them.',
            )}
          </p>
          <label className="field-label" htmlFor="workflow-concurrency">
            {t('Simultaneous operations')}
          </label>
          <select
            id="workflow-concurrency"
            className="field"
            value={draft.concurrency}
            onChange={(event) =>
              setDraft({ ...draft, concurrency: Number(event.target.value) })
            }
          >
            {[1, 2, 3, 4].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
          <label className="flex min-h-11 items-center gap-2">
            <input
              type="checkbox"
              checked={draft.schedule_enabled}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  schedule_enabled: event.target.checked,
                  timing: draft.timing ?? {
                    kind: 'interval',
                    interval_minutes: 60,
                  },
                })
              }
            />
            {t('Enable workflow schedule')}
          </label>
          {draft.schedule_enabled && (
            <>
              <label className="field-label" htmlFor="workflow-timing">
                {t('When')}
              </label>
              <select
                id="workflow-timing"
                className="field"
                value={draft.timing?.kind}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    timing:
                      event.target.value === 'daily'
                        ? { kind: 'daily', at_time: '09:00' }
                        : { kind: 'interval', interval_minutes: 60 },
                  })
                }
              >
                <option value="interval">{t('Every N minutes')}</option>
                <option value="daily">{t('Daily at')}</option>
              </select>
              {draft.timing?.kind === 'daily' ? (
                <>
                  <label htmlFor="workflow-time" className="field-label">
                    {t('Time (server clock)')}
                  </label>
                  <input
                    id="workflow-time"
                    type="time"
                    required
                    className="field"
                    value={draft.timing.at_time ?? ''}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        timing: {
                          ...draft.timing,
                          kind: 'daily',
                          at_time: event.target.value,
                        },
                      })
                    }
                  />
                  <fieldset>
                    <legend className="field-label">
                      {t('On days (none = every day)')}
                    </legend>
                    <div className="flex flex-wrap gap-1">
                      {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(
                        (day, index) => (
                          <button
                            key={day}
                            type="button"
                            className="btn btn-default"
                            aria-pressed={
                              draft.timing?.days?.includes(index) ?? false
                            }
                            onClick={() => {
                              const days = draft.timing?.days ?? []
                              setDraft({
                                ...draft,
                                timing: {
                                  ...draft.timing,
                                  kind: 'daily',
                                  days: days.includes(index)
                                    ? days.filter((value) => value !== index)
                                    : [...days, index].sort(),
                                },
                              })
                            }}
                          >
                            {t(day)}
                          </button>
                        ),
                      )}
                    </div>
                  </fieldset>
                </>
              ) : (
                <>
                  <label htmlFor="workflow-interval" className="field-label">
                    {t('Interval (minutes)')}
                  </label>
                  <input
                    id="workflow-interval"
                    type="number"
                    min={1}
                    max={40320}
                    required
                    className="field"
                    value={draft.timing?.interval_minutes ?? 60}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        timing: {
                          kind: 'interval',
                          interval_minutes: Number(event.target.value),
                        },
                      })
                    }
                  />
                </>
              )}
              {picker}
              <p className="text-ink-dim">
                {t(
                  'Scheduled work uses these exact profile versions. After editing a profile, review and save this schedule again.',
                )}
              </p>
            </>
          )}
          {error && (
            <p className="text-danger break-words" role="alert">
              {t(error)}
            </p>
          )}
        </form>
      </Modal>
      <Modal
        open={!!review}
        title={t('Review workflow run')}
        onClose={() => {
          if (!busy) setReview(null)
        }}
        footer={
          <>
            <button
              className="btn btn-default"
              disabled={busy}
              onClick={() => setReview(null)}
            >
              {t('Cancel')}
            </button>
            <button
              className="btn btn-primary"
              disabled={
                busy || !validTargets || (!!review?.retry && !acknowledged)
              }
              onClick={start}
            >
              {t('Start workflow')}
            </button>
          </>
        }
      >
        <h3 className="font-medium">{review?.workflow.definition.name}</h3>
        <ol className="mt-2 space-y-2">
          {review?.workflow.definition.steps.map((step, index) => (
            <li key={index} className="break-words">
              {index + 1}. {step.name} · {t(actions[step.action])}
              {step.url && (
                <p className="font-mono text-ink-dim break-all">{step.url}</p>
              )}
              {step.seconds && (
                <p>{t('Wait {count} seconds', { count: step.seconds })}</p>
              )}
            </li>
          ))}
        </ol>
        {picker}
        {review?.retry && (
          <label className="mt-3 flex min-h-11 items-start gap-2 text-warn">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            {t(
              'I understand that incomplete profiles restart at step one and previously completed steps may repeat. Successful profiles are excluded.',
            )}
          </label>
        )}
        {error && (
          <p role="alert" className="text-danger break-words mt-3">
            {t(error)}
          </p>
        )}
      </Modal>
      <Modal
        open={!!history}
        title={t('Run history')}
        onClose={() => setHistory(null)}
        width={720}
        footer={
          <button className="btn btn-default" onClick={() => setHistory(null)}>
            {t('Close')}
          </button>
        }
      >
        <button className="btn btn-default" onClick={refreshRuns}>
          {t('Refresh')}
        </button>
        {cancelError && (
          <p role="alert" className="mt-2 text-danger">
            {t('Workflow request failed. Refresh and try again.')}
          </p>
        )}
        {!runs.length ? (
          <p className="mt-3">{t('No workflow runs yet.')}</p>
        ) : (
          <>
            <label className="field-label mt-3" htmlFor="workflow-run">
              {t('Workflow run')}
            </label>
            <select
              id="workflow-run"
              className="field"
              value={currentRun?.id}
              onChange={(event) => setRunId(event.target.value)}
            >
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {new Date(run.started_at).toLocaleString(locale)} ·{' '}
                  {t(states[run.state] ?? 'Unknown')}
                </option>
              ))}
            </select>
            {currentRun && (
              <div className="mt-4 space-y-3" aria-live="polite">
                <p>{t(states[currentRun.state] ?? 'Unknown')}</p>
                <progress
                  className="w-full"
                  aria-label={t('Workflow progress')}
                  max={Math.max(1, currentRun.targets.length)}
                  value={
                    currentRun.targets.filter(
                      (target) => !['queued', 'running'].includes(target.state),
                    ).length
                  }
                />
                {active(currentRun) ? (
                  <>
                    <button
                      className="btn btn-default"
                      disabled={currentRun.cancel_requested || busy}
                      onClick={async () => {
                        if (!history) return
                        setBusy(true)
                        try {
                          await workflowsAPI.cancel(history.id, currentRun.id)
                          await refreshRuns()
                        } catch {
                          setCancelError(true)
                        } finally {
                          setBusy(false)
                        }
                      }}
                    >
                      {t('Cancel remaining work')}
                    </button>
                    <p className="text-ink-dim">
                      {t(
                        'Cancellation stops waiting profiles and later steps. An accepted navigation or launch finishes before the run closes its own browser.',
                      )}
                    </p>
                  </>
                ) : (
                  history &&
                  currentRun.targets.some(
                    (target) => target.state !== 'completed',
                  ) && (
                    <button
                      className="btn btn-default"
                      onClick={() => openReview(history, currentRun)}
                    >
                      {t('Review incomplete profiles')}
                    </button>
                  )
                )}
                {currentRun.targets.map((target) => (
                  <article
                    key={target.profile_id}
                    className="rounded border border-line p-3 break-words"
                  >
                    <h3 className="font-medium">
                      {profiles.find(
                        (profile) => profile.id === target.profile_id,
                      )?.name ?? target.profile_id}{' '}
                      · {t(states[target.state] ?? 'Unknown')}
                    </h3>
                    {target.message && (
                      <p className="mt-1 text-ink-dim">{t(target.message)}</p>
                    )}
                    <ol className="mt-2 space-y-1">
                      {target.steps.map((step, index) => (
                        <li key={index}>
                          {index + 1}. {step.name} ·{' '}
                          {t(states[step.state] ?? 'Unknown')}
                        </li>
                      ))}
                    </ol>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </Modal>
      <ConfirmDialog
        open={!!deleting}
        title={t('Delete workflow')}
        body={t(
          'Delete this workflow and its retained run history? Profiles and browser data are kept.',
        )}
        destructive
        confirmLabel={t('Delete')}
        onCancel={() => setDeleting(null)}
        onConfirm={async () => {
          if (!deleting || busy) return
          setBusy(true)
          try {
            await workflowsAPI.remove(deleting)
            setDeleting(null)
            await load()
          } catch (error) {
            fail(error)
            setDeleting(null)
          } finally {
            setBusy(false)
          }
        }}
      />
    </section>
  )
}
