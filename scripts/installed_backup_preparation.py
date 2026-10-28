"""Shared additive backup preparation for disposable installed acceptance only."""

import re

from camoufox_pm.core.operation_diagnostics import validated_diagnostic


def backup_skip_code(skipped, profile_id):
    """Fixed report codes from public backup failures, never their private text."""
    if not isinstance(skipped, list) or len(skipped) != 1:
        return "other"
    item = skipped[0]
    if not isinstance(item, dict) or item.get("profile_id") != profile_id:
        return "other"
    reason = item.get("reason")
    if not isinstance(reason, str):
        return "other"
    fixed_reasons = {
        "Profile not found": "missing_profile",
        f"Profile with ID {profile_id} not found": "missing_profile",
        "Invalid profile identity": "invalid_identity",
        "Profile directory must be inside the managed profiles directory": "storage_path",
        "Private update path permits other users": "private_permissions",
        "Private paths cannot contain symlinks/reparse points": "private_link",
        "Synchronized profile is blocked; reconcile checkout and recovery before continuing": "sync_fence",
        "Invalid backup ID": "backup_identity",
        "Invalid backup path": "backup_path",
        "Profile archive is larger than the import limit": "archive_limit",
    }
    if reason in fixed_reasons:
        return fixed_reasons[reason]
    if re.fullmatch(
        rf"Profile {re.escape(profile_id)} is leased by another holder"
        r"(?: \([A-Za-z0-9_.:-]{1,512}\))?",
        reason,
    ):
        return "busy"
    if reason == "Profile data directory is missing; backup was not created":
        return "missing_data"
    if reason.startswith("Stored proxy credentials could not be decrypted. "):
        return "credentials"
    if reason.startswith("Cannot archive symbolic link: "):
        return "symbolic_link"
    if re.match(r"^\[Errno [0-9]{1,3}\] ", reason):
        return "filesystem"
    return "other"


def prepare_profile_backup(device, profile_id, report=None, *, wait, api_error):
    # Use the public manual operation instead of waiting for the next automatic
    # scheduler tick. A successful response also means its profile lease has
    # been released. Only this additive preparation may retry a busy response;
    # accepted workflow/launch mutations are never replayed.
    diagnostics = {"attempts": 0, "busy_responses": 0, "prepared": False}
    if report is not None:
        report["backup_preparation"] = diagnostics

    def create():
        diagnostics["attempts"] += 1
        try:
            result = device.call("/system/backups", {"profile_ids": [profile_id]})["data"]
        except api_error as error:
            if error.status == 409:
                diagnostics["busy_responses"] += 1
                return False
            raise
        diagnostics["prepared"] = any(
            item["profile_id"] == profile_id for item in result["backups"]
        )
        if not diagnostics["prepared"]:
            skipped = result.get("skipped")
            if (
                isinstance(skipped, list)
                and len(skipped) == 1
                and isinstance(skipped[0], dict)
                and skipped[0].get("profile_id") == profile_id
            ):
                failure = validated_diagnostic(skipped[0].get("diagnostic"), area="backup")
                if failure is not None:
                    diagnostics["operation_diagnostic"] = failure
            code = backup_skip_code(result.get("skipped"), profile_id)
            diagnostics["last_skip_code"] = code
            if code == "filesystem":
                match = re.match(r"^\[Errno ([0-9]{1,3})\] ", result["skipped"][0]["reason"])
                diagnostics["errno"] = int(match[1]) if match else None
            if code == "busy":
                # Per-profile admission can be returned as a partial 200 batch,
                # while whole-backup admission is a 409. Both are additive-only
                # preparation retries; never repeat a browser/workflow launch.
                diagnostics["busy_responses"] += 1
                return False
            # Export no arbitrary skip/error text, which may contain file paths
            # or secrets. A non-busy backup failure requires investigation.
            diagnostics["skipped"] = bool(result.get("skipped"))
            raise RuntimeError("Installed acceptance backup preparation was skipped")
        return True

    # If a preceding restart deliberately terminated the installed process and
    # its automatic backup batch still owns the recovery lease, the normal
    # five-minute crash expiry must elapse. Observe public admission for up to
    # that TTL plus one scheduler interval; never clear/shorten a live lease.
    wait(create, seconds=360)
