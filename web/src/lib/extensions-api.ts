import { apiRequest } from "./api";

export interface ExtensionMetadata {
  id?: string;
  name: string;
  version: string;
  addon_id: string;
  manifest_version: number;
  permissions: string[];
  sha256: string;
  unpacked_bytes: number;
  row_version?: number;
  created_at?: string;
}
export interface ProfileExtensions {
  row_version: number;
  extensions: (ExtensionMetadata & { id: string })[];
}
interface Envelope<T> {
  data: T;
}
export const extensionsAPI = {
  async list() {
    return (
      await apiRequest<Envelope<{ extensions: ExtensionMetadata[] }>>(
        "/api/v1/extensions",
      )
    ).data.extensions;
  },
  async upload(file: File) {
    const body = new FormData();
    body.append("file", file);
    return (
      await apiRequest<Envelope<ExtensionMetadata>>("/api/v1/extensions", {
        method: "POST",
        body,
      })
    ).data;
  },
  async remove(id: string, version: number) {
    await apiRequest(
      `/api/v1/extensions/${encodeURIComponent(id)}?row_version=${version}`,
      { method: "DELETE" },
    );
  },
  async profile(id: string) {
    return (
      await apiRequest<Envelope<ProfileExtensions>>(
        `/api/v1/profiles/${encodeURIComponent(id)}/extensions`,
      )
    ).data;
  },
  async assign(
    id: string,
    rowVersion: number,
    extensionIds: string[],
    retainedRefs: string[] = [],
  ) {
    return (
      await apiRequest<Envelope<ProfileExtensions>>(
        `/api/v1/profiles/${encodeURIComponent(id)}/extensions`,
        {
          method: "PUT",
          body: JSON.stringify({
            row_version: rowVersion,
            extension_ids: extensionIds,
            retained_refs: retainedRefs,
          }),
        },
      )
    ).data;
  },
};
