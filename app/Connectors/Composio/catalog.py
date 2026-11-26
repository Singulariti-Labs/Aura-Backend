from .client import get_composio
from .models import Toolkit, ToolkitCategory, ToolkitPage


def list_toolkits(limit: int, cursor: str | None, category: str | None) -> ToolkitPage:
    page = get_composio().toolkits.list(
        limit=limit, cursor=cursor, category=category, sort_by="usage"
    )
    return ToolkitPage(
        items=[
            Toolkit(
                slug=item.slug,
                name=item.name,
                description=item.meta.description,
                logo_url=item.meta.logo,
                categories=[
                    ToolkitCategory(id=category.id, name=category.name)
                    for category in item.meta.categories
                ],
                requires_connection=not item.no_auth,
                managed_auth_available=bool(item.composio_managed_auth_schemes),
            )
            for item in page.items
        ],
        next_cursor=page.next_cursor,
    )
