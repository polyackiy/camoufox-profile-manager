"""Fixed recovery text must not become a generic 5xx disclosure channel."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from camoufox_pm.api.errors import ApiError, BrowserCloseError, install_error_handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plain", "spoofed_code", "subclass", "safe_close"])
async def test_operator_5xx_redaction_requires_the_exact_fixed_error_type(kind):
    class PrivateSubclass(BrowserCloseError):
        pass

    errors = {
        "plain": HTTPException(500, "private path and process diagnostic"),
        "spoofed_code": ApiError(
            500, "private path and process diagnostic", code="browser_exit_unconfirmed"
        ),
        "subclass": PrivateSubclass(),
        "safe_close": BrowserCloseError(),
    }
    error = errors[kind]
    error.detail = "private path and process diagnostic"
    if isinstance(error, ApiError):
        error.details = ["private structured diagnostic"]
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/operation")
    async def operation(request: Request):
        request.state.principal = SimpleNamespace(role="operator")
        raise error

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/operation")
    assert response.status_code == 500
    assert "private" not in response.text
    body = response.json()["error"]
    assert body["details"] is None
    assert body["message"] == (
        "Browser exit could not be confirmed. Close remaining browsers, then retry "
        "Stop or restart the manager."
        if kind == "safe_close"
        else "Operation failed. Ask an administrator to inspect the server log."
    )
