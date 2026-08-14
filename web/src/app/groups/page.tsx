'use client'

import { useCallback, useEffect, useState } from 'react'
import { Layers, Pencil, Plus, Trash2 } from 'lucide-react'

import { EmptyState } from '@/components/empty-state'
import { ConfirmDialog, Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { groupsAPI, type Group } from '@/lib/api'

export default function GroupsPage() {
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
      toast('error', 'Name is required')
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await groupsAPI.update(editing.id, { name: name.trim(), description: description.trim() })
        toast('ok', 'Group updated', name.trim())
      } else {
        await groupsAPI.create({ name: name.trim(), description: description.trim() })
        toast('ok', 'Group created', name.trim())
      }
      setFormOpen(false)
      load()
    } catch (err) {
      toast('error', editing ? 'Could not update group' : 'Could not create group', String(err))
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
      toast('ok', 'Group deleted', group.name)
      load()
    } catch (err) {
      toast('error', 'Could not delete group', String(err))
    }
  }

  return (
    <>
      <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <h1 className="text-[14px] font-semibold">Groups</h1>
        <span className="font-mono text-ink-faint">{groups.length}</span>
        <button className="btn btn-primary ml-auto" onClick={openCreate}>
          <Plus size={14} strokeWidth={2.5} />
          New group
        </button>
      </header>

      {error ? (
        <EmptyState
          icon={<Layers size={18} />}
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
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<Layers size={18} />}
          title="No groups yet"
          body="Groups organise profiles by purpose or client. Assign a profile to a group when you create or edit it."
          action={
            <button className="btn btn-primary" onClick={openCreate}>
              <Plus size={14} strokeWidth={2.5} />
              Create a group
            </button>
          }
        />
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="py-2 pl-5 pr-4 font-medium">Name</th>
              <th className="py-2 pr-4 font-medium">ID</th>
              <th className="py-2 pr-4 font-medium">Description</th>
              <th className="py-2 pr-4 font-medium">Profiles</th>
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
                      aria-label={`Edit ${group.name}`}
                      onClick={() => openEdit(group)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="btn btn-ghost h-7 w-7 p-0 hover:text-danger"
                      aria-label={`Delete ${group.name}`}
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
        title={editing ? 'Edit group' : 'New group'}
        onClose={() => setFormOpen(false)}
        width={440}
        footer={
          <>
            <button className="btn btn-default" onClick={() => setFormOpen(false)}>
              Cancel
            </button>
            <button type="submit" form="group-form" className="btn btn-primary" disabled={saving}>
              {editing ? 'Save changes' : 'Create group'}
            </button>
          </>
        }
      >
        <form id="group-form" onSubmit={save} className="flex flex-col gap-3">
          <div>
            <label className="field-label" htmlFor="group-name">
              Name
            </label>
            <input
              id="group-name"
              className="field"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Client A"
              autoFocus
              required
            />
          </div>
          <div>
            <label className="field-label" htmlFor="group-desc">
              Description
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
        title="Delete group"
        body={
          deleting
            ? `"${deleting.name}" will be removed. Its ${deleting.profile_count} profile(s) are kept and become ungrouped.`
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
