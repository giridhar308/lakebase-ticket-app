"""
Lakebase (Postgres) data access layer for the Ticket Now app.

Connection details are NOT read from environment variables. Every function
here takes an explicit `conn` dict describing the connection (see app.py's
`get_connection_from_secrets`, which builds it from a Databricks secret):

    conn = {
        "host": "...",                 # required
        "port": 5432,                  # optional, default 5432
        "dbname": "databricks_postgres",  # optional
        "user": "...",                 # required
        "password": "...",             # optional — see below
        "sslmode": "require",          # optional, default "require"
        "credential_name": "...",      # optional — see below
    }

Tables are always created in the 'public' schema.

If `password` is not supplied, a short-lived OAuth token is minted on demand
via the Databricks SDK's WorkspaceClient, using `credential_name` to identify
which Lakebase endpoint/instance to mint it for:
  - Autoscaling ("Lakebase") projects: "projects/<id>/branches/<id>/endpoints/<id>"
  - Provisioned Database Instances: the plain instance name
"""

import uuid
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

STATUS_OPTIONS = ["open", "in_progress", "resolved", "closed"]
PRIORITY_OPTIONS = ["low", "medium", "high", "urgent"]

_SCHEMA = "public"
_TICKETS = f"{_SCHEMA}.tickets"
_MESSAGES = f"{_SCHEMA}.ticket_messages"

# --- Parsing the user-supplied Lakebase URL -----------------------------------


def parse_lakebase_url(raw: str) -> dict:
    """Parse a Lakebase URL into connection components.

    Accepts either a bare host (e.g. "ep-xxxx.database.<region>.cloud.databricks.com")
    or a full Postgres connection string
    (e.g. "postgresql://user:password@host:5432/databricks_postgres").
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Lakebase URL is required.")

    if "://" not in raw:
        return {"host": raw}

    parsed = urlparse(raw)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("Could not parse a host from that URL.")

    result: dict = {"host": parsed.hostname}
    if parsed.port:
        result["port"] = parsed.port
    if parsed.username:
        result["user"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    dbname = parsed.path.lstrip("/")
    if dbname:
        result["dbname"] = dbname
    return result


def _table_names():
    return _TICKETS, _MESSAGES


# --- Connection ---------------------------------------------------------------

_workspace_client = None


def _get_workspace_client():
    global _workspace_client
    if _workspace_client is None:
        from databricks.sdk import WorkspaceClient

        _workspace_client = WorkspaceClient()
    return _workspace_client


def _generate_oauth_token(conn: dict) -> str:
    """Mint a short-lived Lakebase credential via the Databricks SDK."""
    identifier = conn.get("credential_name")
    if not identifier:
        raise RuntimeError(
            "No password was supplied and no endpoint/instance name was given "
            "to mint an OAuth token. Either include a password in the Lakebase "
            "URL, or set 'credential_name'."
        )

    w = _get_workspace_client()

    if "/" in identifier:
        # Autoscaling ("Lakebase") project: projects/<id>/branches/<id>/endpoints/<id>
        cred = w.postgres.generate_database_credential(endpoint=identifier)
    else:
        # Provisioned Database Instance: plain instance name
        cred = w.database.generate_database_credential(
            request_id=str(uuid.uuid4()), instance_names=[identifier]
        )
    return cred.token


def get_connection(conn: dict):
    host = conn.get("host")
    if not host:
        raise RuntimeError("A Lakebase host is required.")
    user = conn.get("user")
    if not user:
        raise RuntimeError("A Postgres user is required.")

    password = conn.get("password") or _generate_oauth_token(conn)

    return psycopg2.connect(
        host=host,
        port=conn.get("port", 5432),
        dbname=conn.get("dbname", "databricks_postgres"),
        user=user,
        password=password,
        sslmode=conn.get("sslmode", "require"),
        connect_timeout=10,
    )


@contextmanager
def db_cursor(conn: dict, commit: bool = False):
    connection = get_connection(conn)
    try:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            connection.commit()
    finally:
        connection.close()


def test_connection(conn: dict):
    """Raises if `conn` cannot open a working connection."""
    with db_cursor(conn) as cur:
        cur.execute("SELECT 1")


# --- Schema setup -------------------------------------------------------------

_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {tickets} (
    ticket_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title       VARCHAR(500) NOT NULL,
    status      VARCHAR(50)  NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'in_progress', 'resolved', 'closed')),
    priority    VARCHAR(20)  NOT NULL DEFAULT 'medium',
    assigned_to VARCHAR(255),
    created_by  VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- Migration for tables created by an earlier version of this app.
ALTER TABLE {tickets} ADD COLUMN IF NOT EXISTS priority VARCHAR(20) NOT NULL DEFAULT 'medium';
ALTER TABLE {tickets} ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(255);
ALTER TABLE {tickets} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS {messages} (
    message_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id     BIGINT NOT NULL REFERENCES {tickets}(ticket_id) ON DELETE CASCADE,
    message_text  TEXT NOT NULL,
    author        VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket_id ON {messages} (ticket_id);
CREATE INDEX IF NOT EXISTS idx_tickets_priority ON {tickets} (priority);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON {tickets} (assigned_to);
"""

# Added separately (rather than as a column-level CHECK) because ALTER TABLE
# ... ADD CONSTRAINT has no "IF NOT EXISTS" in Postgres — this DO block makes
# adding it idempotent across repeated init_db() calls.
_PRIORITY_CONSTRAINT_SQL = f"""
DO $$
BEGIN
    ALTER TABLE {_TICKETS} ADD CONSTRAINT tickets_priority_check
        CHECK (priority IN ('low', 'medium', 'high', 'urgent'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
"""


def init_db(conn: dict):
    """Create (or migrate) the tickets/ticket_messages tables in 'public'."""
    ddl = _DDL_TEMPLATE.format(tickets=_TICKETS, messages=_MESSAGES)
    with db_cursor(conn, commit=True) as cur:
        cur.execute(ddl)
        cur.execute(_PRIORITY_CONSTRAINT_SQL)


# --- Queries: tickets -----------------------------------------------------------


def list_tickets(
    conn: dict,
    status_filter: str | None = None,
    priority_filter: str | None = None,
    assignee_filter: str | None = None,
):
    tickets, _ = _table_names()
    q = (
        f"SELECT ticket_id, title, status, priority, assigned_to, created_by, "
        f"created_at, updated_at FROM {tickets}"
    )
    clauses = []
    params: list = []
    if status_filter and status_filter != "All":
        clauses.append("status = %s")
        params.append(status_filter)
    if priority_filter and priority_filter != "All":
        clauses.append("priority = %s")
        params.append(priority_filter)
    if assignee_filter and assignee_filter != "All":
        clauses.append("assigned_to = %s")
        params.append(assignee_filter)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC"
    with db_cursor(conn) as cur:
        cur.execute(q, params)
        return cur.fetchall()


def list_distinct_assignees(conn: dict):
    tickets, _ = _table_names()
    q = (
        f"SELECT DISTINCT assigned_to FROM {tickets} "
        "WHERE assigned_to IS NOT NULL AND assigned_to <> '' ORDER BY assigned_to"
    )
    with db_cursor(conn) as cur:
        cur.execute(q)
        return [row["assigned_to"] for row in cur.fetchall()]


def create_ticket(
    conn: dict,
    title: str,
    created_by: str,
    priority: str = "medium",
    assigned_to: str | None = None,
) -> int:
    if priority not in PRIORITY_OPTIONS:
        raise ValueError(f"Invalid priority: {priority!r}")
    tickets, _ = _table_names()
    q = (
        f"INSERT INTO {tickets} (title, created_by, priority, assigned_to) "
        "VALUES (%s, %s, %s, %s) RETURNING ticket_id"
    )
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (title, created_by, priority, assigned_to or None))
        return cur.fetchone()["ticket_id"]


def update_ticket(
    conn: dict,
    ticket_id: int,
    *,
    status: str | None = None,
    priority: str | None = None,
    assigned_to: str | None = None,
    clear_assignee: bool = False,
):
    """Update any combination of status/priority/assigned_to for a ticket.

    Only fields explicitly passed are changed. Pass clear_assignee=True to
    unassign a ticket (set assigned_to to NULL) rather than leaving it as-is.
    """
    tickets, _ = _table_names()
    sets = ["updated_at = now()"]
    params: list = []

    if status is not None:
        if status not in STATUS_OPTIONS:
            raise ValueError(f"Invalid status: {status!r}")
        sets.append("status = %s")
        params.append(status)

    if priority is not None:
        if priority not in PRIORITY_OPTIONS:
            raise ValueError(f"Invalid priority: {priority!r}")
        sets.append("priority = %s")
        params.append(priority)

    if clear_assignee:
        sets.append("assigned_to = NULL")
    elif assigned_to is not None:
        sets.append("assigned_to = %s")
        params.append(assigned_to or None)

    if len(sets) == 1:  # nothing besides updated_at was requested
        return

    params.append(ticket_id)
    q = f"UPDATE {tickets} SET {', '.join(sets)} WHERE ticket_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, params)


def update_ticket_status(conn: dict, ticket_id: int, status: str):
    """Thin wrapper kept for the quick inline status control."""
    update_ticket(conn, ticket_id, status=status)


def delete_ticket(conn: dict, ticket_id: int):
    """Delete a ticket and (via ON DELETE CASCADE) all of its messages."""
    tickets, _ = _table_names()
    q = f"DELETE FROM {tickets} WHERE ticket_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (ticket_id,))


# --- Queries: messages ------------------------------------------------------------


def list_messages(conn: dict, ticket_id: int):
    _, messages = _table_names()
    q = (
        f"SELECT message_id, message_text, author, created_at FROM {messages} "
        "WHERE ticket_id = %s ORDER BY created_at ASC"
    )
    with db_cursor(conn) as cur:
        cur.execute(q, (ticket_id,))
        return cur.fetchall()


def add_message(conn: dict, ticket_id: int, message_text: str, author: str):
    _, messages = _table_names()
    q = f"INSERT INTO {messages} (ticket_id, message_text, author) VALUES (%s, %s, %s)"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (ticket_id, message_text, author))


def update_message(conn: dict, message_id: int, message_text: str):
    _, messages = _table_names()
    q = f"UPDATE {messages} SET message_text = %s WHERE message_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (message_text, message_id))


# --- Dashboard aggregates ----------------------------------------------------------


def get_dashboard_stats(conn: dict, days: int = 14) -> dict:
    """Aggregate counts/metrics for the dashboard tab.

    `avg_resolution_hours` is an approximation: it's measured from
    `created_at` to the last `updated_at` on tickets currently resolved or
    closed, which assumes the ticket wasn't edited again after being
    resolved.
    """
    tickets, _ = _table_names()
    with db_cursor(conn) as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {tickets}")
        total = cur.fetchone()["n"]

        cur.execute(f"SELECT status, COUNT(*) AS n FROM {tickets} GROUP BY status")
        by_status = {row["status"]: row["n"] for row in cur.fetchall()}

        cur.execute(f"SELECT priority, COUNT(*) AS n FROM {tickets} GROUP BY priority")
        by_priority = {row["priority"]: row["n"] for row in cur.fetchall()}

        cur.execute(
            f"SELECT date_trunc('day', created_at) AS day, COUNT(*) AS n "
            f"FROM {tickets} "
            f"WHERE created_at >= now() - (interval '1 day' * %s) "
            f"GROUP BY day ORDER BY day",
            (days,),
        )
        by_day = cur.fetchall()

        cur.execute(
            f"SELECT AVG(EXTRACT(EPOCH FROM (updated_at - created_at)) / 3600.0) AS avg_hours "
            f"FROM {tickets} WHERE status IN ('resolved', 'closed')"
        )
        avg_row = cur.fetchone()
        avg_resolution_hours = avg_row["avg_hours"] if avg_row else None

        cur.execute(
            f"SELECT COUNT(*) AS n FROM {tickets} "
            "WHERE assigned_to IS NULL OR assigned_to = ''"
        )
        unassigned = cur.fetchone()["n"]

    return {
        "total": total,
        "by_status": by_status,
        "by_priority": by_priority,
        "by_day": by_day,
        "avg_resolution_hours": avg_resolution_hours,
        "unassigned": unassigned,
    }
