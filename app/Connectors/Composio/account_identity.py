from composio_client import APIError

from .client import get_composio
from .models import Connection


def _label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if 0 < len(value) <= 320 and value.isprintable() else None


def resolve_account_label(owner: str, account: Connection, state: object) -> str | None:
    """Project only a display label from an account already scoped to this owner."""
    if not account.is_active:
        return None

    if hasattr(state, "model_dump"):
        state = state.model_dump(by_alias=True)
    values = state.get("val") if isinstance(state, dict) else None
    display_name = _label(values.get("displayName")) if isinstance(values, dict) else None
    if account.toolkit != "gmail" or (display_name and "@" in display_name):
        return display_name

    try:
        profile = get_composio().tools.proxy(
            endpoint="https://gmail.googleapis.com/gmail/v1/users/me/profile",
            method="GET",
            connected_account_id=account.id,
            user_id=owner,
        )
    except APIError:
        return display_name

    if profile.status == 200 and isinstance(profile.data, dict):
        return _label(profile.data.get("emailAddress")) or display_name
    return display_name
