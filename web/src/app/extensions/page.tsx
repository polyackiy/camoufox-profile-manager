"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Puzzle, Upload, Trash2, Search } from "lucide-react";
import { useI18n } from "@/components/i18n-provider";
import { useToast } from "@/components/toast";
import { EmptyState } from "@/components/empty-state";
import { Modal } from "@/components/modal";
import { ProfileExtensionsModal } from "@/components/profile-extensions-modal";
import { type Profile } from "@/lib/api";
import { profilesForSelection } from "@/lib/profile-selection";
import { extensionsAPI, type ExtensionMetadata } from "@/lib/extensions-api";

export default function ExtensionsPage() {
  const { t } = useI18n();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<ExtensionMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<ExtensionMetadata | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [choose, setChoose] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const load = useCallback(async () => {
    try {
      const data = await extensionsAPI.list();
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
    // Read the local extension library.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);
  const shown = useMemo(
    () =>
      items.filter((item) =>
        `${item.name} ${item.addon_id} ${item.version}`
          .toLowerCase()
          .includes(search.toLowerCase()),
      ),
    [items, search],
  );
  async function upload(file: File) {
    if (file.size > 16 * 1024 * 1024) {
      setActionError(t("Extension upload must be at most 16 MB"));
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const item = await extensionsAPI.upload(file);
      await load();
      toast("ok", t("Extension uploaded"), `${item.name} ${item.version}`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  async function manage() {
    setChoose(true);
    setBusy(true);
    setActionError(null);
    try {
      const data = await profilesForSelection();
      setProfiles(data);
      setProfileId(data[0]?.id ?? "");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!deleting?.id || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await extensionsAPI.remove(deleting.id, deleting.row_version ?? 0);
      setDeleting(null);
      await load();
      toast("ok", t("Library extension deleted"));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <header className="sticky top-0 z-20 flex min-h-[52px] flex-wrap items-center gap-3 border-b border-line bg-canvas/85 px-5 py-2 backdrop-blur">
        <h1 className="text-[14px] font-semibold">{t("Extension library")}</h1>
        <span className="font-mono text-ink-faint">{items.length}</span>
        <input
          className="field max-w-[260px]"
          aria-label={t("Search extensions")}
          placeholder={t("Search name, ID or version")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <div className="ml-auto flex gap-2">
          <button className="btn btn-default" onClick={manage} disabled={busy}>
            {t("Manage profile extensions")}
          </button>
          <button
            className="btn btn-primary"
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            <Upload size={13} aria-hidden="true" />
            {busy ? t("Uploading…") : t("Upload XPI")}
          </button>
          <input
            ref={input}
            type="file"
            accept=".xpi,application/x-xpinstall,application/zip"
            className="hidden"
            aria-label={t("Choose local XPI")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) upload(file);
            }}
          />
        </div>
      </header>
      <div className="border-b border-line px-5 py-4 text-ink-dim">
        <p>
          {t(
            "Upload Firefox extensions from local XPI files. Review permissions before assigning them to stopped profiles. Uploads are stored locally; no remote downloads or automatic updates.",
          )}
        </p>
        <p className="mt-1">
          {t(
            "Profiles and templates keep independent snapshots even after a library extension is deleted. Profile archives include assigned snapshots.",
          )}
        </p>
        {actionError && !deleting && !choose && (
          <p className="mt-3 text-danger" role="alert">
            {t(actionError)}
          </p>
        )}
      </div>
      {error ? (
        <EmptyState
          icon={<Puzzle size={18} />}
          title={t("Cannot load extensions")}
          body={t(error)}
          action={
            <button className="btn btn-default" onClick={load}>
              {t("Retry")}
            </button>
          }
        />
      ) : loading ? (
        <p role="status" className="px-5 py-8">
          {t("Loading…")}
        </p>
      ) : !shown.length ? (
        <EmptyState
          icon={items.length ? <Search size={18} /> : <Puzzle size={18} />}
          title={t(
            items.length ? "No matching extensions" : "No extensions yet",
          )}
          body={t(
            "Choose a local Firefox XPI with an explicit add-on ID. Maximum upload size is 16 MB.",
          )}
          action={
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => input.current?.click()}
            >
              {t("Upload XPI")}
            </button>
          }
        />
      ) : (
        <div className="divide-y divide-line">
          {shown.map((item) => (
            <article key={item.id} className="flex flex-wrap gap-4 px-5 py-4">
              <div className="min-w-0 flex-1">
                <h2 className="font-medium">
                  {item.name}
                  <span className="ml-2 font-mono text-ink-dim">
                    {item.version}
                  </span>
                </h2>
                <p className="mt-1 break-all font-mono text-[11px] text-ink-dim">
                  {item.addon_id}
                </p>
                <p className="mt-1 text-ink-dim">
                  {t("Manifest V{version} · {size} KB", {
                    version: item.manifest_version,
                    size: (item.unpacked_bytes / 1024).toFixed(0),
                  })}
                </p>
                <details className="mt-2 text-ink-dim">
                  <summary className="cursor-pointer">
                    {t("Permissions")} · {item.permissions.length}
                  </summary>
                  <ul className="mt-1 list-inside list-disc break-all font-mono text-[11px]">
                    {item.permissions.length ? (
                      item.permissions.map((permission) => (
                        <li key={permission}>{permission}</li>
                      ))
                    ) : (
                      <li>{t("No declared permissions")}</li>
                    )}
                  </ul>
                </details>
              </div>
              <button
                className="btn btn-ghost self-start"
                disabled={busy}
                aria-label={t("Delete {name}", { name: item.name })}
                onClick={() => {
                  setDeleting(item);
                  setActionError(null);
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
                {t("Delete")}
              </button>
            </article>
          ))}
        </div>
      )}
      <Modal
        open={!!deleting}
        title={t("Delete library extension")}
        width={500}
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
            <button className="btn btn-danger" onClick={remove} disabled={busy}>
              {t("Delete")}
            </button>
          </>
        }
      >
        <p>
          {t('Delete "{name}" from the library?', { name: deleting?.name })}
        </p>
        <p className="mt-3 text-ink-dim">
          {t(
            "Existing profiles and templates keep their copied extension snapshots.",
          )}
        </p>
        {actionError && (
          <p role="alert" className="mt-3 text-danger">
            {t(actionError)}
          </p>
        )}
      </Modal>
      <Modal
        open={choose}
        title={t("Choose profile")}
        width={500}
        onClose={() => setChoose(false)}
        footer={
          <>
            <button
              className="btn btn-default"
              onClick={() => setChoose(false)}
            >
              {t("Cancel")}
            </button>
            <button
              className="btn btn-primary"
              disabled={busy || !profileId}
              onClick={() => {
                setProfile(
                  profiles.find((item) => item.id === profileId) ?? null,
                );
                setChoose(false);
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
            value={profileId}
            onChange={(event) => setProfileId(event.target.value)}
          >
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-3 text-ink-dim">
          {t("Stop the selected profile before changing extensions.")}
        </p>
        {actionError && (
          <p role="alert" className="mt-3 text-danger">
            {t(actionError)}
          </p>
        )}
      </Modal>
      {profile && (
        <ProfileExtensionsModal
          profile={profile}
          onClose={() => setProfile(null)}
          onSaved={() => load()}
        />
      )}
    </>
  );
}
