"""Role and explicit profile grants, shared by API and scheduled work."""


class AccessControl:
    def __init__(self, storage):
        self.storage = storage

    async def visible_profile_ids(self, principal, permission="view"):
        if permission not in {"view", "edit", "launch", "export"}:
            raise ValueError("Unknown permission")
        if principal.kind in {"legacy_api_key", "anonymous_local", "system"}:
            return None
        user = await self.storage.get_user_by_username(principal.username)
        if not user:
            return set()
        if user["role"] == "administrator":
            return None
        if permission in {"edit", "launch"} and user["role"] != "operator":
            return set()
        grants = await self.storage.list_profile_grants(user["username"])
        return {g["profile_id"] for g in grants if permission != "export" or g["can_export"]}

    async def can_profile(self, principal, profile_id, permission="view"):
        ids = await self.visible_profile_ids(principal, permission)
        return ids is None or profile_id in ids
