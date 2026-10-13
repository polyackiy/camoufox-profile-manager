"""Keep admission held for the complete request, including streamed uploads."""

from starlette.responses import JSONResponse

from camoufox_pm.core.maintenance import MaintenanceBlocked


class MaintenanceMiddleware:
    def __init__(self, app, gate):
        self.app = app
        self.gate = gate

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Reads can also renew login sessions or inspect mutable files. Gate all
        # API requests while maintenance runs; health/static UI remain available.
        path = scope.get("path", "")
        # These routes use the usual router authentication and do not touch the
        # application data. Cancellation is serialized by gate ownership.
        controls = {
            f"{prefix}/system/update-recovery{suffix}"
            for prefix in ("/api", "/api/v1")
            for suffix in ("", "/cancel", "/apply", "/resume", "/rollback")
        }
        if path in controls or not path.startswith("/api/"):
            return await self.app(scope, receive, send)
        try:
            with self.gate.operation():
                await self.app(scope, receive, send)
        except MaintenanceBlocked as exc:
            response = JSONResponse(
                {"detail": str(exc)}, status_code=503, headers={"Retry-After": "5"}
            )
            await response(scope, receive, send)
