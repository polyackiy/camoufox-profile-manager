"use client";
import { useEffect, useState } from "react";
import { Modal } from "./modal";
import { useI18n } from "./i18n-provider";
import { isStaleWrite, type BrowserSettings } from "@/lib/api";
import { templatesAPI, type ProfileTemplate } from "@/lib/templates-api";
import { extensionsAPI, type ExtensionMetadata } from "@/lib/extensions-api";

export function TemplateEditor({
  template,
  onClose,
  onSaved,
  onReload,
}: {
  template: ProfileTemplate | null;
  onClose: () => void;
  onSaved: (value: ProfileTemplate) => void;
  onReload: () => void;
}) {
  const { t } = useI18n();
  const [name, setName] = useState(template?.name ?? "");
  const [notes, setNotes] = useState(template?.notes ?? "");
  const [tags, setTags] = useState(template?.tags.join(", ") ?? "");
  const [settings, setSettings] = useState<BrowserSettings>(
    template?.browser_settings ?? {
      os: "windows",
      screen: "1920x1080",
      languages: ["en-US", "en"],
      webrtc_mode: "replace",
      browser_ui: "desktop",
    },
  );
  const [advanced, setAdvanced] = useState(JSON.stringify(settings, null, 2));

  const [library, setLibrary] = useState<ExtensionMetadata[]>([]);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [replaceExtensions, setReplaceExtensions] = useState(!template);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    extensionsAPI
      .list()
      .then((items) => {
        setLibrary(items);
        setSelected(
          new Set(
            items
              .filter((item) =>
                template?.extensions.some(
                  (extension) => extension.sha256 === item.sha256,
                ),
              )
              .map((item) => item.id!),
          ),
        );
      })
      .catch((err) => setLibraryError(String(err)));
  }, [template]);
  function change(patch: Partial<BrowserSettings>) {
    try {
      const base = JSON.parse(advanced);
      if (!base || typeof base !== "object" || Array.isArray(base))
        throw new Error(t("Browser settings must be a JSON object."));
      const next = { ...base, ...patch };
      setSettings(next);
      setAdvanced(JSON.stringify(next, null, 2));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  async function save() {
    if (!name.trim() || busy || stale) return;
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(advanced);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error(t("Browser settings must be a JSON object."));
      const draft = {
        name: name.trim(),
        notes: notes || null,
        tags: tags
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        browser_settings: parsed,
        ...(replaceExtensions ? { extension_ids: [...selected] } : {}),
      };
      const saved = template
        ? await templatesAPI.update(template, draft)
        : await templatesAPI.create(draft);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStale(isStaleWrite(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={t(template ? "Edit template" : "New template")}
      width={660}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button className="btn btn-default" disabled={busy} onClick={onClose}>
            {t("Cancel")}
          </button>
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={
              busy || stale || !name.trim() || (!template && !!libraryError)
            }
          >
            {busy ? t("Saving…") : t("Save template")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className="block">
          {t("Template name")}
          <input
            autoFocus
            className="field mt-1"
            maxLength={255}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            {t("Operating system")}
            <select
              className="field mt-1"
              value={settings.os}
              onChange={(event) => change({ os: event.target.value })}
            >
              {["windows", "macos", "linux"].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("Screen resolution")}
            <input
              className="field mt-1"
              value={settings.screen}
              onChange={(event) => change({ screen: event.target.value })}
            />
          </label>
        </div>
        <label className="block">
          {t("Languages")}
          <input
            className="field mt-1"
            value={(settings.languages ?? []).join(", ")}
            onChange={(event) =>
              change({
                languages: event.target.value
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            {t("Timezone")}
            <input
              className="field mt-1"
              placeholder={t("Follow proxy location")}
              value={settings.timezone ?? ""}
              onChange={(event) =>
                change({ timezone: event.target.value || null })
              }
            />
          </label>
          <label>
            {t("WebRTC")}
            <select
              className="field mt-1"
              value={settings.webrtc_mode ?? "replace"}
              onChange={(event) => change({ webrtc_mode: event.target.value })}
            >
              <option value="replace">{t("Replace")}</option>
              <option value="none">{t("Disable")}</option>
              <option value="real">{t("Real")}</option>
              <option value="forward">{t("Forward")}</option>
            </select>
          </label>
        </div>
        <label className="block">
          {t("Tags")}
          <input
            className="field mt-1"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
          <span className="mt-1 block text-ink-dim">
            {t("Separate tags with commas.")}
          </span>
        </label>
        <label className="block">
          {t("Notes")}
          <textarea
            className="field mt-1 min-h-[70px]"
            maxLength={1000}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
        <details>
          <summary className="cursor-pointer font-medium">
            {t("All browser settings")}
          </summary>
          <p className="my-2 text-ink-dim">
            {t(
              "Edit browser settings as JSON. Cookies, proxies and fingerprints are not accepted in templates.",
            )}
          </p>
          <textarea
            className="field min-h-[180px] font-mono text-[12px]"
            aria-label={t("Browser settings JSON")}
            value={advanced}
            onChange={(event) => setAdvanced(event.target.value)}
          />
        </details>
        <fieldset className="border-t border-line pt-4">
          <legend className="font-medium">{t("Extensions")}</legend>
          {template && (
            <>
              <p className="mb-2 text-ink-dim">
                {template.extensions
                  .map((item) => `${item.name} ${item.version}`)
                  .join(", ") || t("No extensions")}
              </p>
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  checked={replaceExtensions}
                  onChange={(event) =>
                    setReplaceExtensions(event.target.checked)
                  }
                />
                {t("Replace saved extension snapshots")}
              </label>
            </>
          )}
          {replaceExtensions &&
            library.map((item) => (
              <label key={item.id} className="mt-2 flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={selected.has(item.id!)}
                  onChange={() =>
                    setSelected((old) => {
                      const next = new Set(old);
                      if (next.has(item.id!)) next.delete(item.id!);
                      else next.add(item.id!);
                      return next;
                    })
                  }
                />
                <span>
                  {item.name}{" "}
                  <span className="font-mono text-ink-dim">{item.version}</span>
                  <span className="block break-all text-[11px] text-ink-dim">
                    {item.permissions.join(", ") ||
                      t("No declared permissions")}
                  </span>
                </span>
              </label>
            ))}
          {libraryError && (
            <p className="text-danger" role="alert">
              {t("Could not load extension library")}
            </p>
          )}
        </fieldset>
        <p className="text-ink-dim">
          {t(
            "New profiles receive fresh browser data and an independently generated identity on first launch.",
          )}
        </p>
        {error && (
          <div role="alert">
            <p className="text-danger">{t(error)}</p>
            {stale && (
              <button className="btn btn-default mt-2" onClick={onReload}>
                {t("Reload saved template")}
              </button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
