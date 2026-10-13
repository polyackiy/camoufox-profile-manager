"use client";
import { useAuth } from "./login-gate";
import { useEffect, useState } from "react";
import { Modal } from "./modal";
import { useI18n } from "./i18n-provider";
import { useToast } from "./toast";
import type { Profile } from "@/lib/api";
import {
  extensionsAPI,
  type ExtensionMetadata,
  type ProfileExtensions,
} from "@/lib/extensions-api";

export function ProfileExtensionsModal({
  profile,
  onClose,
  onSaved,
}: {
  profile: Profile;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const { canManageAccess } = useAuth();
  const toast = useToast();
  const [library, setLibrary] = useState<ExtensionMetadata[]>([]);
  const [current, setCurrent] = useState<ProfileExtensions | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [retained, setRetained] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    try {
      const [items, saved] = await Promise.all([
        canManageAccess ? extensionsAPI.list() : Promise.resolve([]),
        extensionsAPI.profile(profile.id),
      ]);
      setLibrary(items);
      setCurrent(saved);
      setSelected(
        new Set(
          items
            .filter((item) =>
              saved.extensions.some(
                (extension) => extension.sha256 === item.sha256,
              ),
            )
            .map((item) => item.id!),
        ),
      );
      setRetained(
        new Set(
          saved.extensions
            .filter(
              (extension) =>
                !items.some((item) => item.sha256 === extension.sha256),
            )
            .map((item) => item.id),
        ),
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  useEffect(() => {
    // Load independent library metadata and assigned snapshots.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);
  function toggle(id: string, isRetained = false) {
    const setter = isRetained ? setRetained : setSelected;
    setter((old) => {
      const next = new Set(old);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  async function save() {
    if (!canManageAccess || !current || busy) return;
    setBusy(true);
    setError(null);
    try {
      await extensionsAPI.assign(
        profile.id,
        current.row_version,
        [...selected],
        [...retained],
      );
      toast("ok", t("Profile extensions saved"));
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const orphaned =
    current?.extensions.filter(
      (extension) => !library.some((item) => item.sha256 === extension.sha256),
    ) ?? [];
  return (
    <Modal
      open
      title={t("Extensions for {name}", { name: profile.name })}
      width={600}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button className="btn btn-default" onClick={onClose} disabled={busy}>
            {t("Cancel")}
          </button>
          {canManageAccess && <button
            className="btn btn-primary"
            onClick={save}
            disabled={busy || !current || selected.size + retained.size > 16}
          >
            {busy ? t("Saving…") : t("Save extensions")}
          </button>}
        </>
      }
    >
      {canManageAccess ? <p className="mb-4 text-ink-dim">
        {t(
          "Changes apply to stopped profiles on the next launch. Review permissions before assigning an extension. Each profile keeps an independent snapshot.",
        )}
      </p>
      : <p className="mb-4 text-ink-dim">{t("Assigned extension snapshots")}</p>}
      {!current && !error && <p role="status">{t("Loading…")}</p>}
      {[
        ...library.map((item) => ({ item, key: item.id!, orphan: false })),
        ...orphaned.map((item) => ({ item, key: item.id, orphan: true })),
      ].map(({ item, key, orphan }) => (
        <div className="border-b border-line py-3" key={key}>
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={(orphan ? retained : selected).has(key)}
              onChange={() => toggle(key, orphan)}
              disabled={busy || !canManageAccess}
            />
            <span>
              <span className="font-medium">{item.name}</span>
              <span className="ml-2 font-mono text-ink-dim">
                {item.version}
              </span>
              <span className="block break-all font-mono text-[11px] text-ink-dim">
                {item.addon_id}
              </span>
              {orphan && canManageAccess && (
                <span className="block text-ink-dim">
                  {t("Profile snapshot; library entry was removed")}
                </span>
              )}
            </span>
          </label>
          <details className="ml-7 mt-2 text-ink-dim">
            <summary className="cursor-pointer">
              {t("Permissions")} · {item.permissions.length}
            </summary>
            <p className="mt-1 break-all font-mono text-[11px]">
              {item.permissions.join(", ") || t("No declared permissions")}
            </p>
          </details>
        </div>
      ))}
      {current && !library.length && !orphaned.length && (
        <p className="text-ink-dim">
          {t("Upload a local XPI in the extension library first.")}
        </p>
      )}
      <p className="mt-3 text-ink-dim">
        {t("{count} selected · maximum 16", {
          count: selected.size + retained.size,
        })}
      </p>
      {error && (
        <div className="mt-3" role="alert">
          <p className="text-danger">{t(error)}</p>
          <button
            className="btn btn-default mt-2"
            onClick={load}
            disabled={busy}
          >
            {t("Reload saved extensions")}
          </button>
        </div>
      )}
    </Modal>
  );
}
