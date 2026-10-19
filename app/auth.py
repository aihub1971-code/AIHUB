"""Single-owner HTTP Basic authentication for the bootstrap stage."""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_setting

_basic = HTTPBasic(
    auto_error=False,
    scheme_name="OwnerBasicAuth",
    realm="AI HUB",
)


def require_owner(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> str:
    expected_username = get_setting("APP_USERNAME")
    expected_password = get_setting("APP_PASSWORD")

    # Fail closed if Render's owner credentials have not both been configured.
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "auth_not_configured",
                "message": "Set APP_USERNAME and APP_PASSWORD in the service environment.",
            },
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="AI HUB"'},
        )

    username_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        expected_username.encode("utf-8"),
    )
    password_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="AI HUB"'},
        )

    # Single-owner mode for now. User identities and scopes can be added later.
    return "owner"
