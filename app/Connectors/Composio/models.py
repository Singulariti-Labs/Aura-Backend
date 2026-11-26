from pydantic import BaseModel, Field, HttpUrl

TOOLKIT_PATTERN = r"^[a-z0-9_][a-z0-9_-]{0,127}$"
CONNECTION_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


class ToolkitCategory(BaseModel):
    id: str
    name: str


class Toolkit(BaseModel):
    slug: str
    name: str
    description: str
    logo_url: str
    categories: list[ToolkitCategory]
    requires_connection: bool
    managed_auth_available: bool


class ToolkitPage(BaseModel):
    items: list[Toolkit]
    next_cursor: str | None = None


class Connection(BaseModel):
    id: str
    toolkit: str
    alias: str | None = None
    account_label: str | None = None
    status: str
    is_disabled: bool
    created_at: str

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE" and not self.is_disabled


class ConnectionPage(BaseModel):
    items: list[Connection]
    next_cursor: str | None = None


class ConnectRequest(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=100)


class ConnectLink(BaseModel):
    connection_id: str
    redirect_url: HttpUrl
