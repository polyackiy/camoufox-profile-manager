import { apiRequest, type BrowserSettings, type Profile } from "./api";
import type { ExtensionMetadata } from "./extensions-api";

export interface ProfileTemplate {
  id: string;
  name: string;
  browser_settings: BrowserSettings;
  notes: string | null;
  tags: string[];
  extensions: ExtensionMetadata[];
  row_version: number;
  created_at: string;
  updated_at: string;
}
export interface TemplateDraft {
  name: string;
  browser_settings: BrowserSettings;
  notes?: string | null;
  tags?: string[];
  extension_ids?: string[];
}
interface Envelope<T> {
  data: T;
}
export const templatesAPI = {
  async list() {
    return (
      await apiRequest<Envelope<{ templates: ProfileTemplate[] }>>(
        "/api/v1/templates",
      )
    ).data.templates;
  },
  async create(draft: TemplateDraft) {
    return (
      await apiRequest<Envelope<ProfileTemplate>>("/api/v1/templates", {
        method: "POST",
        body: JSON.stringify(draft),
      })
    ).data;
  },
  async fromProfile(profile: Profile, name: string) {
    return (
      await apiRequest<Envelope<ProfileTemplate>>(
        "/api/v1/templates/from-profile",
        {
          method: "POST",
          body: JSON.stringify({
            name,
            profile_id: profile.id,
            profile_row_version: profile.row_version,
          }),
        },
      )
    ).data;
  },
  async update(template: ProfileTemplate, draft: TemplateDraft) {
    return (
      await apiRequest<Envelope<ProfileTemplate>>(
        `/api/v1/templates/${encodeURIComponent(template.id)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ ...draft, row_version: template.row_version }),
        },
      )
    ).data;
  },
  async remove(template: ProfileTemplate) {
    await apiRequest(
      `/api/v1/templates/${encodeURIComponent(template.id)}?row_version=${template.row_version}`,
      { method: "DELETE" },
    );
  },
  async createProfile(
    template: ProfileTemplate,
    name: string,
    group: string | null = null,
  ) {
    return (
      await apiRequest<Envelope<Profile>>(
        `/api/v1/templates/${encodeURIComponent(template.id)}/profiles`,
        {
          method: "POST",
          body: JSON.stringify({
            row_version: template.row_version,
            name,
            group,
          }),
        },
      )
    ).data;
  },
};
