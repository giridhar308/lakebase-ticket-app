"""
Lakebase (Postgres) data access layer for the ticket support app.

Unlike the previous version, connection details are NOT read from environment
variables at import time. Instead the user pastes a Lakebase URL into the app
at runtime (see the "Connect to Lakebase" form in app.py), and every function
here takes an explicit `conn` dict describing that connection:

    conn = {
        "host": "...",                 # required
        "port": 5432,                  # optional, default 5432
        "dbname": "databricks_postgres",  # optional
        "user": "...",                 # required
        "password": "...",             # optional — see below
        "sslmode": "require",          # optional, default "require"
        "schema": "public",            # optional, default "public"
        "credential_name": "...",      # optional — see below
    }

If `password` is not supplied, a short-lived OAuth token is minted on demand
via the Databricks SDK's WorkspaceClient, using `credential_name` to identify
which Lakebase endpoint/instance to mint it for:
  - Autoscaling ("Lakebase") projects: "projects/<id>/branches/<id>/endpoints/<id>"
  - Provisioned Database Instances: the plain instance name

When this app is deployed as a Databricks App, `WorkspaceClient()` (no
arguments) automatically authenticates as the app's own identity — no host or
secret needs to be supplied for that part.
"""

import re
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

STATUS_OPTIONS = ["open", "in_progress", "resolved", "closed"]

# --- Parsing the user-supplied Lakebase URL -----------------------------------


def parse_lakebase_url(raw: str) -> dict:
    """Parse a Lakebase URL entered by the user into connection components.

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


def _schema_name(conn: dict) -> str:
    """Always return 'public' schema regardless of connection settings."""
    return "public"


def _table_names(conn: dict):
    schema = _schema_name(conn)
    return f"{schema}.tickets", f"{schema}.ticket_messages"


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
            "URL, or fill in 'Endpoint / instance name' under Advanced settings."
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
    created_by  VARCHAR(255) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {messages} (
    message_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id     BIGINT NOT NULL REFERENCES {tickets}(ticket_id) ON DELETE CASCADE,
    message_text  TEXT NOT NULL,
    author        VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket_id
    ON {messages} (ticket_id);
"""


def init_db(conn: dict):
    """Initialize database tables in the public schema."""
    tickets, messages = _table_names(conn)
    ddl = _DDL_TEMPLATE.format(tickets=tickets, messages=messages)
    with db_cursor(conn, commit=True) as cur:
        # Tables are always created in the public schema
        cur.execute(ddl)


# --- Queries -------------------------------------------------------------------


def list_tickets(conn: dict, status_filter: str | None = None):
    tickets, _ = _table_names(conn)
    q = f"SELECT ticket_id, title, status, created_by, created_at FROM {tickets}"
    params: tuple = ()
    if status_filter and status_filter != "All":
        q += " WHERE status = %s"
        params = (status_filter,)
    q += " ORDER BY created_at DESC"
    with db_cursor(conn) as cur:
        cur.execute(q, params)
        return cur.fetchall()


def create_ticket(conn: dict, title: str, created_by: str) -> int:
    tickets, _ = _table_names(conn)
    q = f"INSERT INTO {tickets} (title, created_by) VALUES (%s, %s) RETURNING ticket_id"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (title, created_by))
        return cur.fetchone()["ticket_id"]


def update_ticket_status(conn: dict, ticket_id: int, status: str):
    if status not in STATUS_OPTIONS:
        raise ValueError(f"Invalid status: {status!r}")
    tickets, _ = _table_names(conn)
    q = f"UPDATE {tickets} SET status = %s WHERE ticket_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (status, ticket_id))


def list_messages(conn: dict, ticket_id: int):
    _, messages = _table_names(conn)
    q = (
        f"SELECT message_id, message_text, author, created_at FROM {messages} "
        "WHERE ticket_id = %s ORDER BY created_at ASC"
    )
    with db_cursor(conn) as cur:
        cur.execute(q, (ticket_id,))
        return cur.fetchall()


def add_message(conn: dict, ticket_id: int, message_text: str, author: str):
    _, messages = _table_names(conn)
    q = f"INSERT INTO {messages} (ticket_id, message_text, author) VALUES (%s, %s, %s)"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (ticket_id, message_text, author))


def update_message(conn: dict, message_id: int, message_text: str):
    """Update an existing message's text."""
    _, messages = _table_names(conn)
    q = f"UPDATE {messages} SET message_text = %s WHERE message_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (message_text, message_id))


def update_message(conn: dict, message_id: int, message_text: str):
    _, messages = _table_names(conn)
    q = f"UPDATE {messages} SET message_text = %s WHERE message_id = %s"
    with db_cursor(conn, commit=True) as cur:
        cur.execute(q, (message_text, message_id))
