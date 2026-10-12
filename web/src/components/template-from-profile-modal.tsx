"use client";
import { useState } from "react";
import { Modal } from "./modal";
import { useI18n } from "./i18n-provider";
import { useToast } from "./toast";
import type { Profile } from "@/lib/api";
import { templatesAPI } from "@/lib/templates-api";

export function TemplateFromProfileModal({
  profile,
  onClose,
  onSaved,
}: {
  profile: Profile;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [name, setName] = useState(profile.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await templatesAPI.fromProfile(profile, name.trim());
      toast("ok", t("Template saved"));
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={t("Save as template")}
      width={500}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button className="btn btn-default" onClick={onClose} disabled={busy}>
            {t("Cancel")}
          </button>
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={busy || !name.trim()}
          >
            {busy ? t("Saving…") : t("Save template")}
          </button>
        </>
      }
    >
      <label className="block font-medium" htmlFor="template-profile-name">
        {t("Template name")}
      </label>
      <input
        id="template-profile-name"
        autoFocus
        className="field mt-2"
        maxLength={255}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <p className="mt-3 text-ink-dim">
        {t(
          "Copies browser settings, notes, tags and extension snapshots. Cookies, logins, proxy credentials and pinned fingerprints are excluded. Stop the profile before saving a template.",
        )}
      </p>
      {error && (
        <p className="mt-3 text-danger" role="alert">
          {t(error)}
        </p>
      )}
    </Modal>
  );
}
