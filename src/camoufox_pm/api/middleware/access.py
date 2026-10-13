"""Isolate actors and audit outcomes after mutation/stream completion."""

import json
import re

from camoufox_pm.api.access import request_principal, request_session_hash


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = request_principal.set(None)
        session_context = request_session_hash.set(None)
        status_code = 500
        expected_length = None
        sent_length = 0
        finished = False
        body_result = None
        response_object_id = None

        async def audited_send(message):
            nonlocal \
                status_code, \
                expected_length, \
                sent_length, \
                finished, \
                body_result, \
                response_object_id
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = dict(message.get("headers", []))
                if b"content-length" in headers:
                    expected_length = int(headers[b"content-length"])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if sent_length == 0 and len(body) <= 256 * 1024:
                    try:
                        value = json.loads(body)
                        if isinstance(value, dict):
                            response_object_id = value.get("id")
                            if value.get("success") is False:
                                body_result = "failure"
                            elif (
                                value.get("errors")
                                or value.get("skipped")
                                or value.get("not_found")
                            ):
                                body_result = "partial"
                    except (ValueError, UnicodeDecodeError):
                        pass
                sent_length += len(body)
                finished = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, audited_send)
        finally:
            try:
                principal = scope.get("state", {}).get("principal")
                route_name = getattr(scope.get("route"), "name", "")
                path = scope.get("path", "")
                mutation = scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}
                # Successful auth/admin writes include their audit event in the
                # storage operation. Offline recovery cannot write to this DB.
                managed = (
                    ("/auth/" in path or "/access/" in path)
                    and status_code < 400
                    and route_name != "export_audit"
                )
                offline = "/system/update-recovery" in path
                if (
                    principal
                    and route_name
                    and not managed
                    and not offline
                    and (mutation or "export" in route_name or status_code in {403, 404})
                ):
                    from camoufox_pm.api.dependencies import get_storage_manager

                    params = scope.get("path_params", {})
                    object_id = next(
                        (
                            params[key]
                            for key in (
                                "profile_id",
                                "schedule_id",
                                "group_id",
                                "template_id",
                                "proxy_id",
                                "extension_id",
                            )
                            if key in params
                        ),
                        response_object_id if isinstance(response_object_id, str) else None,
                    )
                    if object_id is not None and not re.fullmatch(
                        r"(?:[A-Za-z0-9]{8}|[0-9a-fA-F-]{36})", object_id
                    ):
                        object_id = None
                    result = (
                        "denied"
                        if status_code in {403, 404}
                        else "failure"
                        if status_code >= 400
                        else body_result or "success"
                    )
                    if not finished or (
                        expected_length is not None and sent_length != expected_length
                    ):
                        result = "interrupted"
                    await get_storage_manager().append_audit(
                        principal, route_name, object_id, result
                    )
            finally:
                request_principal.reset(token)
                request_session_hash.reset(session_context)
