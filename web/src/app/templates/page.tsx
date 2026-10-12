"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { FileStack, Plus, Search, Pencil, Trash2, Copy } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";
import { EmptyState } from "@/components/empty-state";
import { Modal } from "@/components/modal";
import { TemplateEditor } from "@/components/template-editor";
import { TemplateFromProfileModal } from "@/components/template-from-profile-modal";
import { useToast } from "@/components/toast";
import { isStaleWrite, type Profile } from "@/lib/api";
import { profilesForSelection } from "@/lib/profile-selection";
import { templatesAPI, type ProfileTemplate } from "@/lib/templates-api";

export default function TemplatesPage() {
  const { t } = useI18n();
  const toast = useToast();
  const [items, setItems] = useState<ProfileTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [editor, setEditor] = useState<{
    template: ProfileTemplate | null;
  } | null>(null);
  const [deleting, setDeleting] = useState<ProfileTemplate | null>(null);
  const [creating, setCreating] = useState<ProfileTemplate | null>(null);
  const [profileName, setProfileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [createStale, setCreateStale] = useState(false);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [chooseSource, setChooseSource] = useState(false);
  const [sourceId, setSourceId] = useState("");
  const [source, setSource] = useState<Profile | null>(null);
  const load = useCallback(async () => {
    try {
      const data = await templatesAPI.list();
      setItems(data);
      setError(null);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    // Load template metadata for this page.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);
  const shown = useMemo(
    () =>
      items.filter((item) =>
        `${item.name} ${item.tags.join(" ")} ${item.browser_settings.os}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [items, search],
  );
  async function reloadDraft() {
    const data = await load();
    if (!data) return;
    const latest = data.find((item) => item.id === editor?.template?.id);
    if (latest) setEditor({ template: latest });
    else setEditor(null);
  }
  async function pickProfile() {
    setActionError(null);
    setChooseSource(true);
    setBusy(true);
    try {
      const data = await profilesForSelection();
      setProfiles(data);
      setSourceId(data[0]?.id ?? "");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!deleting || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await templatesAPI.remove(deleting);
      setDeleting(null);
      await load();
      toast("ok", t("Template deleted"));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function createProfile() {
    if (!creating || !profileName.trim() || busy || createStale) return;
    setBusy(true);
    setActionError(null);
    try {
      await templatesAPI.createProfile(creating, profileName.trim());
      setCreating(null);
      toast("ok", t("Fresh profile created"), profileName);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
      setCreateStale(isStaleWrite(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header className="sticky top-0 z-20 flex min-h-[52px] flex-wrap items-center gap-3 border-b border-line bg-canvas/85 px-5 py-2 backdrop-blur">
        <h1 className="text-[14px] font-semibold">{t("Profile templates")}</h1>
        <span className="font-mono text-ink-faint">{items.length}</span>
        <input
          className="field max-w-[260px]"
          aria-label={t("Search templates")}
          placeholder={t("Search name, tags or OS")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="ml-auto flex gap-2">
          <button className="btn btn-default" onClick={pickProfile}>
            <Copy size={13} aria-hidden="true" />
            {t("From profile")}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => setEditor({ template: null })}
          >
            <Plus size={14} aria-hidden="true" />
            {t("New template")}
          </button>
        </div>
      </header>
      <p className="border-b border-line px-5 py-4 text-ink-dim">
        {t(
          "Reuse browser settings, notes, tags and extension snapshots. Each new profile starts with fresh browser data, no proxy and its own identity. Existing profiles never change when a template is edited.",
        )}
      </p>
      {error ? (
        <EmptyState
          icon={<FileStack size={18} />}
          title={t("Cannot load templates")}
          body={t(error)}
          action={
            <button className="btn btn-default" onClick={load}>
              {t("Retry")}
            </button>
          }
        />
      ) : loading ? (
        <p className="px-5 py-8" role="status">
          {t("Loading…")}
        </p>
      ) : !shown.length ? (
        <EmptyState
          icon={items.length ? <Search size={18} /> : <FileStack size={18} />}
          title={t(items.length ? "No matching templates" : "No templates yet")}
          body={t(
            "Create reusable defaults or save a stopped profile as a template.",
          )}
          action={
            <button
              className="btn btn-primary"
              onClick={() => setEditor({ template: null })}
            >
              {t("New template")}
            </button>
          }
        />
      ) : (
        <div className="grid gap-4 p-5 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((item) => (
            <article
              key={item.id}
              className="rounded-lg border border-line bg-surface p-4"
            >
              <h2 className="font-semibold">{item.name}</h2>
              <p className="mt-1 font-mono text-ink-dim">
                {item.browser_settings.os} · {item.browser_settings.screen}
              </p>
              <p className="mt-2 text-ink-dim">
                {item.tags.join(" · ") || "—"}
              </p>
              <p className="mt-2 text-ink-dim">
                {t("{count} extension snapshots", {
                  count: item.extensions.length,
                })}
              </p>
              {item.notes && (
                <p className="mt-2 line-clamp-3 break-words text-ink-dim">
                  {item.notes}
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  className="btn btn-primary"
                  onClick={() => {
                    setCreating(item);
                    setCreateStale(false);
                    setProfileName(`${item.name} profile`);
                    setActionError(null);
                  }}
                >
                  {t("Create profile")}
                </button>
                <button
                  className="btn btn-default"
                  aria-label={t("Edit {name}", { name: item.name })}
                  onClick={() => setEditor({ template: item })}
                >
                  <Pencil size={13} aria-hidden="true" />
                  {t("Edit")}
                </button>
                <button
                  className="btn btn-ghost"
                  aria-label={t("Delete {name}", { name: item.name })}
                  onClick={() => {
                    setDeleting(item);
                    setActionError(null);
                  }}
                >
                  <Trash2 size={13} aria-hidden="true" />
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      {editor && (
        <TemplateEditor
          key={`${editor.template?.id ?? "new"}:${editor.template?.row_version ?? 0}`}
          template={editor.template}
          onClose={() => setEditor(null)}
          onReload={reloadDraft}
          onSaved={() => {
            setEditor(null);
            load();
            toast("ok", t("Template saved"));
          }}
        />
      )}
      <Modal
        open={!!deleting}
        title={t("Delete template")}
        width={480}
        onClose={() => {
          if (!busy) setDeleting(null);
        }}
        footer={
          <>
            <button
              className="btn btn-default"
              disabled={busy}
              onClick={() => setDeleting(null)}
            >
              {t("Cancel")}
            </button>
            <button className="btn btn-danger" disabled={busy} onClick={remove}>
              {t("Delete")}
            </button>
          </>
        }
      >
        <p>
          {t('Delete "{name}"? Existing profiles keep their settings.', {
            name: deleting?.name,
          })}
        </p>
        {actionError && (
          <div role="alert" className="mt-3">
            <p className="text-danger">{t(actionError)}</p>
            <button
              className="btn btn-default mt-2"
              onClick={async () => {
                const data = await load();
                setDeleting(
                  data?.find((item) => item.id === deleting?.id) ?? null,
                );
                setActionError(null);
              }}
            >
              {t("Reload saved template")}
            </button>
          </div>
        )}
      </Modal>
      <Modal
        open={!!creating}
        title={t("Create profile from template")}
        width={500}
        onClose={() => {
          if (!busy) setCreating(null);
        }}
        footer={
          <>
            <button
              className="btn btn-default"
              disabled={busy}
              onClick={() => setCreating(null)}
            >
              {t("Cancel")}
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || createStale || !profileName.trim()}
              onClick={createProfile}
            >
              {busy ? t("Creating…") : t("Create profile")}
            </button>
          </>
        }
      >
        <label>
          {t("Profile name")}
          <input
            className="field mt-2"
            value={profileName}
            maxLength={255}
            onChange={(event) => setProfileName(event.target.value)}
          />
        </label>
        <p className="mt-3 text-ink-dim">
          {t(
            "Cookies, logins, proxy credentials and pinned fingerprints are excluded.",
          )}
        </p>
        {actionError && (
          <div role="alert" className="mt-3">
            <p className="text-danger">{t(actionError)}</p>
            {createStale && (
              <button
                className="btn btn-default mt-2"
                onClick={async () => {
                  const data = await load();
                  setCreating(
                    data?.find((item) => item.id === creating?.id) ?? null,
                  );
                  setCreateStale(false);
                  setActionError(null);
                }}
              >
                {t("Reload saved template")}
              </button>
            )}
          </div>
        )}
      </Modal>
      <Modal
        open={chooseSource}
        title={t("Choose source profile")}
        width={500}
        onClose={() => setChooseSource(false)}
        footer={
          <>
            <button
              className="btn btn-default"
              onClick={() => setChooseSource(false)}
            >
              {t("Cancel")}
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || !sourceId}
              onClick={() => {
                setSource(
                  profiles.find((profile) => profile.id === sourceId) ?? null,
                );
                setChooseSource(false);
              }}
            >
              {t("Continue")}
            </button>
          </>
        }
      >
        <label>
          {t("Profile")}
          <select
            className="field mt-2"
            value={sourceId}
            onChange={(event) => setSourceId(event.target.value)}
          >
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
        {actionError && (
          <p className="mt-3 text-danger" role="alert">
            {t(actionError)}
          </p>
        )}
      </Modal>
      {source && (
        <TemplateFromProfileModal
          profile={source}
          onClose={() => setSource(null)}
          onSaved={() => load()}
        />
      )}
    </>
  );
}
