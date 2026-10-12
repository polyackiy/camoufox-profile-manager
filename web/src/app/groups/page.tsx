'use client'

import { useCallback, useEffect, useState } from 'react'
import { Layers, Pencil, Plus, Trash2 } from 'lucide-react'

import { useI18n } from '@/components/i18n-provider'
import { EmptyState } from '@/components/empty-state'
import { ConfirmDialog, Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { groupsAPI, type Group } from '@/lib/api'

export default function GroupsPage() {
  const { t } = useI18n()
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<Group | null>(null)

  const toast = useToast()

  const load = useCallback(async () => {
    try {
      setError(null)
      const response = await groupsAPI.list()
      setGroups(response.groups)
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
    setName('')
    setDescription('')
    setFormOpen(true)
  }

  function openEdit(group: Group) {
    setEditing(group)
    setName(group.name)
    setDescription(group.description ?? '')
    setFormOpen(true)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) {
      toast('error', t('Name is required'))
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await groupsAPI.update(editing.id, { name: name.trim(), description: description.trim() })
        toast('ok', t('Group updated'), name.trim())
      } else {
        await groupsAPI.create({ name: name.trim(), description: description.trim() })
        toast('ok', t('Group created'), name.trim())
      }
      setFormOpen(false)
      load()
    } catch (err) {
      toast('error', editing ? t('Could not update group') : t('Could not create group'), String(err))
    } finally {
      setSaving(false)
    }
  }

  async function remove() {
    if (!deleting) return
    const group = deleting
    setDeleting(null)
    try {
      await groupsAPI.remove(group.id)
      toast('ok', t('Group deleted'), group.name)
      load()
    } catch (err) {
      toast('error', t('Could not delete group'), String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">{t('Groups')}</h1>
        <span className="font-mono text-ink-faint">{groups.length}</span>
        <button className="btn btn-primary ml-auto" onClick={openCreate}>
          <Plus size={14} strokeWidth={2.5} />
          {t('New group')}
        </button>
      </header>

      {error ? (
        <EmptyState
          icon={<Layers size={18} />}
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
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<Layers size={18} />}
          title={t('No groups yet')}
          body={t('Groups organise profiles by purpose or client. Assign a profile to a group when you create or edit it.')}
          action={
            <button className="btn btn-primary" onClick={openCreate}>
              <Plus size={14} strokeWidth={2.5} />
              {t('Create a group')}
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="py-2 pl-5 pr-4 font-medium">{t('Name')}
            </th>
              <th className="py-2 pr-4 font-medium">{t('ID')}</th>
              <th className="py-2 pr-4 font-medium">{t('Description')}
            </th>
              <th className="py-2 pr-4 font-medium">{t('Profiles')}</th>
              <th className="w-[88px] py-2 pr-5" />
            </tr>
          </thead>
          <tbody>
            {groups.map((group, index) => (
              <tr
                key={group.id}
                className="row-in border-b border-line/60 hover:bg-surface"
                style={{ animationDelay: `${Math.min(index, 12) * 12}ms` }}
              >
                <td className="py-2.5 pl-5 pr-4 font-medium">{group.name}</td>
                <td className="py-2.5 pr-4 font-mono text-ink-faint">{group.id}</td>
                <td className="py-2.5 pr-4 text-ink-dim">{group.description || '—'}</td>
                <td className="py-2.5 pr-4 font-mono text-ink-dim">{group.profile_count}</td>
                <td className="py-2.5 pr-5">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      className="btn btn-ghost h-7 w-7 p-0"
                      aria-label={t('Edit {name}', { name: group.name })}
                      onClick={() => openEdit(group)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0 hover:text-danger"
                      aria-label={t('Delete {name}', { name: group.name })}
                      onClick={() => setDeleting(group)}
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
        title={editing ? t('Edit group') : t('New group')}
        onClose={() => setFormOpen(false)}
        width={440}
        footer={
          <>
            <button className="btn btn-default" onClick={() => setFormOpen(false)}>
              {t('Cancel')}
            </button>
            <button type="submit" form="group-form" className="btn btn-primary" disabled={saving}>
              {editing ? t('Save changes') : t('Create group')}
            </button>
          </>
        }
      >
        <form id="group-form" onSubmit={save} className="flex flex-col gap-3">
          <div>
            <label className="field-label" htmlFor="group-name">
              {t('Name')}
            </label>
            <input
              id="group-name"
              className="field"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('Client A')}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="field-label" htmlFor="group-desc">
              {t('Description')}
            </label>
            <textarea
              id="group-desc"
              className="field resize-y"
              rows={2}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title={t('Delete group')}
        body={
          deleting
            ? t('"{name}" will be removed. Its {count} profile(s) are kept and become ungrouped.', { name: deleting.name, count: deleting.profile_count })
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
