"""Monday.com board API client.

Thin wrapper over the Monday GraphQL endpoint. Provides:
    - fetch_items_in_group(...)  — paginated read of items in a group
    - fetch_item_updates(item_id) — the "comments" feed on an item
    - fetch_user_name(user_id)    — resolve a people-column entry to a name

Why a dedicated client module?
-----------------------------
- Keeps HTTP/GraphQL plumbing in one place.
- Makes tests easy: any other module that wants to avoid real API calls just
  monkeypatches this one module's public functions.
- Gives a portfolio reviewer a single file showing "this is how we talk to
  an external API".
"""

from __future__ import annotations

import json
from typing import Iterator

import requests

from . import config


# ── Raw GraphQL transport ───────────────────────────────────────────

def _headers() -> dict[str, str]:
    """HTTP headers for every Monday request.

    The token lives in the ``Authorization`` header per Monday's API docs.
    Content-Type is JSON because we send query + variables as a JSON body.
    """
    return {
        "Authorization": config.BOARD_API_TOKEN or "",
        "Content-Type": "application/json",
    }


def _execute(query: str, variables: dict) -> dict:
    """Run a GraphQL query/mutation. Returns the ``data`` block or raises.

    Why raise on `errors`?
    ----------------------
    Monday's API returns HTTP 200 even on semantic errors (bad column ID,
    permission issue). The error info lives in a top-level ``errors`` list.
    We raise so the caller gets a proper stack trace instead of silently
    operating on bad data.
    """
    response = requests.post(
        config.BOARD_API_URL,
        json={"query": query, "variables": variables},
        headers=_headers(),
        timeout=30,
    )
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"Board GraphQL error: {payload['errors']}")
    return payload["data"]


# ── Item fetchers ───────────────────────────────────────────────────

def fetch_items_at_status(status_label: str) -> list[dict]:
    """Return every board item whose SCHEDULED column equals status_label.

    We fetch all items on the board (paginated 500 at a time) and filter in
    Python. Monday's GraphQL supports ``items_page`` filters but not a
    status-equality filter directly — the cost of fetching everything is
    fine for a few thousand items.

    Each returned item dict has:
        id, name, column_values[{id, text, value, type}], updates[{created_at, creator{id,name}}]
    """
    # Columns we want to read. List them explicitly so we don't pull the
    # whole column set on every page — saves bandwidth and response size.
    tracked = [
        config.COL_STATUS,
        config.COL_ALLOCATE,
        config.COL_PAID_IN_FULL,
        config.COL_SALESPERSON,
        config.COL_DELIVERY,
    ]
    cols_literal = "[" + ", ".join(f'"{c}"' for c in tracked) + "]"

    items: list[dict] = []
    cursor: str | None = None

    # Python idiom: pagination with a cursor. Loop until cursor is None.
    while True:
        if cursor is None:
            query = f"""
            query ($boardId: [ID!]) {{
              boards(ids: $boardId) {{
                items_page(limit: 500) {{
                  cursor
                  items {{
                    id name
                    column_values(ids: {cols_literal}) {{ id text value type }}
                  }}
                }}
              }}
            }}
            """
            variables = {"boardId": config.BOARD_ID}
        else:
            query = f"""
            query ($boardId: [ID!], $cursor: String!) {{
              boards(ids: $boardId) {{
                items_page(limit: 500, cursor: $cursor) {{
                  cursor
                  items {{
                    id name
                    column_values(ids: {cols_literal}) {{ id text value type }}
                  }}
                }}
              }}
            }}
            """
            variables = {"boardId": config.BOARD_ID, "cursor": cursor}

        data = _execute(query, variables)
        page = data["boards"][0]["items_page"]
        items.extend(page["items"])
        cursor = page.get("cursor")
        if not cursor:
            break

    # Filter by status in Python. Monday's API has a filter syntax but it's
    # fiddly; string-compare after the fact is simpler and costs one pass.
    matches = []
    for item in items:
        for cv in item.get("column_values", []):
            if cv.get("id") == config.COL_STATUS and (cv.get("text") or "").strip() == status_label:
                matches.append(item)
                break
    return matches


def fetch_item_updates(item_id: str) -> list[dict]:
    """Return the comments/updates feed on a single item.

    Each update has:
        id, body (HTML), text_body (plain), created_at (ISO8601), creator{id, name}
    """
    query = """
    query ($itemId: [ID!]) {
      items(ids: $itemId) {
        updates(limit: 50) {
          id
          body
          text_body
          created_at
          creator { id name }
        }
      }
    }
    """
    data = _execute(query, {"itemId": str(item_id)})
    items = data.get("items") or []
    if not items:
        return []
    return items[0].get("updates") or []


def fetch_user_name(user_id: str) -> str | None:
    """Resolve a Monday user id to their display name.

    Used when resolving a people-column entry (the SALESPERSON column) to a
    name we can feed into the email-derivation rule.
    """
    query = """
    query ($ids: [ID!]) {
      users(ids: $ids) { id name email }
    }
    """
    data = _execute(query, {"ids": [str(user_id)]})
    users = data.get("users") or []
    if not users:
        return None
    return users[0].get("name")


# ── Column-value convenience helpers ────────────────────────────────
# These don't call the API — they parse an already-fetched item dict.
# Keeping them in this module because they're tightly coupled to the
# column_values shape the API returns.

def column_value_text(item: dict, column_id: str) -> str:
    """Return the plain-text value of one column on an item, or '' if missing."""
    for cv in item.get("column_values", []):
        if cv.get("id") == column_id:
            return (cv.get("text") or "").strip()
    return ""


def person_ids_on_item(item: dict) -> list[str]:
    """Return the Monday user IDs in the SALESPERSON column of an item.

    People-columns serialize as JSON like
        {"personsAndTeams": [{"id": 67804712, "kind": "person"}]}
    So we have to parse the ``value`` field (not just ``text``).
    """
    for cv in item.get("column_values", []):
        if cv.get("id") == config.COL_SALESPERSON:
            raw = cv.get("value")
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return []
            return [
                str(p["id"])
                for p in parsed.get("personsAndTeams", [])
                if p.get("kind") == "person"
            ]
    return []


def iter_open_items() -> Iterator[dict]:
    """Yield each board item currently at NEEDS ATTENTION.

    Convenience wrapper; most callers want "open items" specifically.
    """
    yield from fetch_items_at_status(config.STATUS_NEEDS_ATTENTION)
