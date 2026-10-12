import { profilesAPI, type Profile } from "./api";

/** Explicit pagination keeps reusable-library selectors within the API page bound. */
export async function profilesForSelection(): Promise<Profile[]> {
  const result: Profile[] = [];
  for (let page = 1; page <= 100; page++) {
    const data = await profilesAPI.getProfiles({ page, per_page: 100 });
    result.push(...data.profiles);
    if (!data.has_next) return result;
  }
  throw new Error(
    "More than 10,000 profiles are available. Use the profile row menu instead.",
  );
}
