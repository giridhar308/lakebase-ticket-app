"""
Ticket Support — a small Databricks App backed by Lakebase (Postgres).

The Lakebase URL is entered live in the sidebar (not baked into a config file
or environment variable) and kept only in Streamlit's session state for the
duration of the browser session. Ticket data itself is always read/written
straight to Lakebase, so it survives a page refresh regardless of how the
connection was made.
"""

import streamlit as st

import db

st.set_page_config(page_title="Ticket Support", page_icon="🎫", layout="wide")

if "conn" not in st.session_state:
    st.session_state.conn = None


def render_connect_form():
    st.title("🎫 Ticket Support")
    st.subheader("🔌 Connect to Lakebase")
    st.caption(
        "Paste your Lakebase URL to connect. This can be a bare host, or a full "
        "connection string like `postgresql://user:password@host:5432/dbname`."
    )

    with st.form("connect_form"):
        url = st.text_input(
            "Lakebase URL",
            placeholder=(
                "postgresql://user:password@ep-xxxx.database."
                "<region>.cloud.databricks.com:5432/databricks_postgres"
            ),
        )

        with st.expander("Advanced connection settings"):
            user_override = st.text_input(
                "Postgres user (overrides URL)",
                help="Required if the URL above doesn't include a username.",
            )
            dbname_override = st.text_input(
                "Database name (overrides URL)", value="", placeholder="databricks_postgres"
            )
            schema = st.text_input("Schema", value="public")
            sslmode = st.selectbox(
                "SSL mode", ["require", "verify-full", "prefer", "disable"], index=0
            )
            credential_name = st.text_input(
                "Endpoint / instance name (for a Databricks-generated credential)",
                placeholder="projects/<id>/branches/<id>/endpoints/<id>  or  my_instance_name",
                help=(
                    "Only needed if the URL above doesn't include a password. "
                    "Used with the Databricks SDK's WorkspaceClient to mint a "
                    "short-lived OAuth token instead."
                ),
            )

        submitted = st.form_submit_button("Connect")

    if not submitted:
        return

    try:
        conn = db.parse_lakebase_url(url)
        if user_override.strip():
            conn["user"] = user_override.strip()
        if dbname_override.strip():
            conn["dbname"] = dbname_override.strip()
        conn["schema"] = schema.strip() or "public"
        conn["sslmode"] = sslmode
        if credential_name.strip():
            conn["credential_name"] = credential_name.strip()
        if not conn.get("user"):
            raise ValueError(
                "A Postgres user is required — include it in the URL "
                "(postgresql://USER:...@host) or fill in the override field."
            )

        with st.spinner("Connecting..."):
            db.test_connection(conn)
            db.init_db(conn)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not connect to Lakebase: {e}")
        return

    st.session_state.conn = conn
    st.toast("Connected to Lakebase.")
    st.rerun()


def render_app():
    conn = st.session_state.conn

    with st.sidebar:
        st.caption(
            f"Connected to **{conn['host']}**  \n"
            f"DB: `{conn.get('dbname', 'databricks_postgres')}` · "
            f"schema `{conn.get('schema', 'public')}`"
        )
        if st.button("Disconnect"):
            st.session_state.conn = None
            st.rerun()

        st.divider()
        st.header("New ticket")
        with st.form("new_ticket_form", clear_on_submit=True):
            new_title = st.text_input("Title")
            new_created_by = st.text_input("Your name / email")
            if st.form_submit_button("Create ticket"):
                if not new_title.strip() or not new_created_by.strip():
                    st.warning("Title and your name are required.")
                else:
                    db.create_ticket(conn, new_title.strip(), new_created_by.strip())
                    st.toast("Ticket created.")
                    st.rerun()

        st.divider()
        status_filter = st.selectbox("Filter by status", ["All"] + db.STATUS_OPTIONS)

    st.title("🎫 Ticket Support")

    try:
        tickets = db.list_tickets(conn, status_filter)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load tickets: {e}")
        st.stop()

    if not tickets:
        st.info("No tickets yet. Create one from the sidebar.")

    for t in tickets:
        header = f"#{t['ticket_id']} · {t['title']} · [{t['status']}]"
        with st.expander(header):
            st.caption(
                f"Created by {t['created_by']} on {t['created_at']:%Y-%m-%d %H:%M}"
            )

            status_col, button_col = st.columns([3, 1])
            with status_col:
                current_index = (
                    db.STATUS_OPTIONS.index(t["status"])
                    if t["status"] in db.STATUS_OPTIONS
                    else 0
                )
                new_status = st.selectbox(
                    "Status",
                    db.STATUS_OPTIONS,
                    index=current_index,
                    key=f"status_select_{t['ticket_id']}",
                )
            with button_col:
                st.write("")
                st.write("")
                if st.button("Update status", key=f"update_status_{t['ticket_id']}"):
                    db.update_ticket_status(conn, t["ticket_id"], new_status)
                    st.toast("Status updated.")
                    st.rerun()

            st.markdown("**Messages**")
            messages = db.list_messages(conn, t["ticket_id"])
            if not messages:
                st.caption("No messages yet.")
            for m in messages:
                st.markdown(
                    f"- **{m['author']}** ({m['created_at']:%Y-%m-%d %H:%M}): "
                    f"{m['message_text']}"
                )

            with st.form(f"add_message_form_{t['ticket_id']}", clear_on_submit=True):
                msg_text = st.text_area(
                    "Add a message", key=f"msg_text_{t['ticket_id']}"
                )
                msg_author = st.text_input(
                    "Your name / email", key=f"msg_author_{t['ticket_id']}"
                )
                if st.form_submit_button("Add message"):
                    if not msg_text.strip() or not msg_author.strip():
                        st.warning("Message and your name are required.")
                    else:
                        db.add_message(
                            conn, t["ticket_id"], msg_text.strip(), msg_author.strip()
                        )
                        st.toast("Message added.")
                        st.rerun()


if st.session_state.conn is None:
    render_connect_form()
else:
    render_app()
