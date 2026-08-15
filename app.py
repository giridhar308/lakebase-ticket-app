"""
Ticket Now — Enterprise Service Management Platform

A ServiceNow-inspired ticket management system built as a Databricks App,
backed by Lakebase (Postgres). Features include:
- Dashboard with ticket/priority/status metrics and trend charts
- Search tickets by ID
- Priority and assignee fields with filters
- Inline status updates, ticket property editing
- Delete a ticket (with a confirmation step)
- Message editing and history
- Multiple color themes
- Clean, modern UI

Connection details are securely stored in Databricks secrets.
"""

import altair as alt
import pandas as pd
import streamlit as st
from databricks.sdk.runtime import dbutils

import db

st.set_page_config(page_title="Ticket Now", page_icon="🎫", layout="wide")

if "conn" not in st.session_state:
    st.session_state.conn = None

# --- Theming --------------------------------------------------------------------
# Gradients chosen for sufficient contrast with white header text in every theme.

THEMES = {
    "Ocean Blue": {
        "gradient": "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        "accent": "#667eea",
    },
    "Sunset": {
        "gradient": "linear-gradient(135deg, #ee0979 0%, #ff6a00 100%)",
        "accent": "#ee0979",
    },
    "Forest": {
        "gradient": "linear-gradient(135deg, #134e5e 0%, #71b280 100%)",
        "accent": "#1baf7a",
    },
    "Midnight": {
        "gradient": "linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%)",
        "accent": "#2c5364",
    },
}

# Status/priority colors are the dataviz skill's validated, reserved palette —
# status colors are never reused for other categorical data (priority instead
# uses an ordinal step of the sequential blue ramp, kept visually distinct).
STATUS_COLORS = {
    "open": "#d03b3b",         # status: critical — unaddressed, needs eyes now
    "in_progress": "#fab219",  # status: warning — being worked, keep watching
    "resolved": "#0ca30c",     # status: good — done
    "closed": "#4a3aa7",       # categorical violet — archived, not a severity signal
}
# fab219 (warning) is very light — white text on it is unreadable, so pill/text
# color is chosen per status rather than assumed white. Validated with
# dataviz's validate_palette.js: this 4-color set passes chroma/CVD/normal-vision
# checks; fab219 fails the categorical lightness band by design (it's the fixed,
# reserved "warning" status color) — mitigated here with dark text + (on charts)
# direct value labels, per the skill's contrast-relief rule.
STATUS_TEXT_COLORS = {
    "open": "#ffffff",
    "in_progress": "#0b0b0b",
    "resolved": "#ffffff",
    "closed": "#ffffff",
}
STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In Progress",
    "resolved": "Resolved",
    "closed": "Closed",
}
# Ordinal blue ramp (sequential hue), light->dark = low->urgent priority.
PRIORITY_COLORS = {
    "low": "#86b6ef",
    "medium": "#5598e7",
    "high": "#2a78d6",
    "urgent": "#184f95",
}
PRIORITY_TEXT_COLORS = {
    "low": "#0b0b0b",
    "medium": "#0b0b0b",
    "high": "#ffffff",
    "urgent": "#ffffff",
}


def inject_theme_css(theme: dict):
    st.markdown(
        f"""
        <style>
        .ticket-header {{
            background: {theme['gradient']};
            padding: 2rem;
            border-radius: 10px;
            color: white;
            margin-bottom: 1.5rem;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }}
        .stButton>button {{
            border-radius: 5px;
        }}
        .stButton>button[kind="primary"] {{
            background-color: {theme['accent']};
            border-color: {theme['accent']};
        }}
        .pill {{
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-right: 4px;
            white-space: nowrap;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_pill(status: str) -> str:
    bg = STATUS_COLORS.get(status, "#4a3aa7")
    fg = STATUS_TEXT_COLORS.get(status, "#ffffff")
    return (
        f'<span class="pill" style="background:{bg};color:{fg};">'
        f"{STATUS_LABELS.get(status, status)}</span>"
    )


def priority_pill(priority: str) -> str:
    bg = PRIORITY_COLORS.get(priority, "#5598e7")
    fg = PRIORITY_TEXT_COLORS.get(priority, "#0b0b0b")
    return f'<span class="pill" style="background:{bg};color:{fg};">{priority.capitalize()}</span>'


# --- Connecting -------------------------------------------------------------------


def get_connection_from_secrets():
    """Load Lakebase connection details from Databricks secrets.

    Expected secrets in scope 'lakebase-app':
    - lakebase_connection_string: Full Postgres connection string
      (postgresql://user:password@host:port/database)

    Note: Tables are always created in the 'public' schema.
    """
    secret_scope = "lakebase-app"

    try:
        connection_string = dbutils.secrets.get(
            scope=secret_scope, key="lakebase_connection_string"
        )
        conn = db.parse_lakebase_url(connection_string)
        if "sslmode" not in conn:
            conn["sslmode"] = "require"
        return conn
    except Exception as e:
        raise ValueError(
            f"Failed to load Lakebase credentials from secret scope '{secret_scope}': {e}\n"
            "Required secrets: lakebase_connection_string"
        )


def initialize_connection():
    """Initialize connection to Lakebase using credentials from secrets."""
    try:
        conn = get_connection_from_secrets()

        with st.spinner("Connecting to Lakebase..."):
            db.test_connection(conn)
            db.init_db(conn)

        st.session_state.conn = conn
        st.toast("Connected to Lakebase.")
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not connect to Lakebase: {e}")
        st.error(
            "Please ensure the 'lakebase-app' secret scope exists with the required secret:\n"
            "- lakebase_connection_string (postgresql://user:password@host:port/database)\n"
            "Note: Tables are created in the 'public' schema."
        )
        st.stop()


# --- Dashboard tab -----------------------------------------------------------------


def render_dashboard(conn):
    stats = db.get_dashboard_stats(conn)

    st.subheader("Overview")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total tickets", stats["total"])
    c2.metric("Open", stats["by_status"].get("open", 0))
    c3.metric("In progress", stats["by_status"].get("in_progress", 0))
    c4.metric("Resolved", stats["by_status"].get("resolved", 0))
    c5.metric("Closed", stats["by_status"].get("closed", 0))

    c6, c7 = st.columns(2)
    avg_hours = stats["avg_resolution_hours"]
    c6.metric(
        "Avg. time to resolve",
        f"{avg_hours:.1f} hrs" if avg_hours is not None else "—",
        help=(
            "Approximate: measured from creation to the last status update "
            "on tickets currently resolved or closed."
        ),
    )
    c7.metric("Unassigned tickets", stats["unassigned"])

    st.divider()

    status_df = pd.DataFrame(
        [
            {
                "status": STATUS_LABELS[s],
                "count": stats["by_status"].get(s, 0),
                "key": s,
            }
            for s in db.STATUS_OPTIONS
        ]
    )
    priority_df = pd.DataFrame(
        [
            {
                "priority": p.capitalize(),
                "count": stats["by_priority"].get(p, 0),
                "key": p,
            }
            for p in db.PRIORITY_OPTIONS
        ]
    )

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("**Tickets by status**")
        base = alt.Chart(status_df).encode(
            x=alt.X(
                "status:N",
                sort=[STATUS_LABELS[s] for s in db.STATUS_OPTIONS],
                title=None,
                axis=alt.Axis(labelColor="#52514e", domainColor="#c3c2b7", tickColor="#c3c2b7"),
            ),
            y=alt.Y(
                "count:Q",
                title=None,
                axis=alt.Axis(labelColor="#898781", gridColor="#e1e0d9", domain=False, tickMinStep=1),
            ),
            color=alt.Color(
                "key:N",
                scale=alt.Scale(domain=list(STATUS_COLORS.keys()), range=list(STATUS_COLORS.values())),
                legend=None,
            ),
            tooltip=[alt.Tooltip("status:N", title="Status"), alt.Tooltip("count:Q", title="Tickets")],
        )
        bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24)
        labels = base.mark_text(dy=-8, color="#0b0b0b").encode(text="count:Q")
        st.altair_chart(
            (bars + labels).properties(height=220).configure_view(strokeWidth=0),
            use_container_width=True,
        )

    with chart_col2:
        st.markdown("**Tickets by priority**")
        pbase = alt.Chart(priority_df).encode(
            x=alt.X(
                "priority:N",
                sort=[p.capitalize() for p in db.PRIORITY_OPTIONS],
                title=None,
                axis=alt.Axis(labelColor="#52514e", domainColor="#c3c2b7", tickColor="#c3c2b7"),
            ),
            y=alt.Y(
                "count:Q",
                title=None,
                axis=alt.Axis(labelColor="#898781", gridColor="#e1e0d9", domain=False, tickMinStep=1),
            ),
            color=alt.Color(
                "key:N",
                scale=alt.Scale(domain=list(PRIORITY_COLORS.keys()), range=list(PRIORITY_COLORS.values())),
                legend=None,
            ),
            tooltip=[alt.Tooltip("priority:N", title="Priority"), alt.Tooltip("count:Q", title="Tickets")],
        )
        pbars = pbase.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=24)
        plabels = pbase.mark_text(dy=-8, color="#0b0b0b").encode(text="count:Q")
        st.altair_chart(
            (pbars + plabels).properties(height=220).configure_view(strokeWidth=0),
            use_container_width=True,
        )

    st.markdown("**Tickets created — last 14 days**")
    day_rows = stats["by_day"]
    if not day_rows:
        st.caption("No tickets created in this window yet.")
    else:
        trend_df = pd.DataFrame([{"day": r["day"], "count": r["n"]} for r in day_rows])
        trend_chart = (
            alt.Chart(trend_df)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=18, color="#2a78d6")
            .encode(
                x=alt.X(
                    "day:T",
                    title=None,
                    axis=alt.Axis(
                        labelColor="#52514e", domainColor="#c3c2b7", tickColor="#c3c2b7", format="%b %d"
                    ),
                ),
                y=alt.Y(
                    "count:Q",
                    title=None,
                    axis=alt.Axis(labelColor="#898781", gridColor="#e1e0d9", domain=False, tickMinStep=1),
                ),
                tooltip=[alt.Tooltip("day:T", title="Date"), alt.Tooltip("count:Q", title="Created")],
            )
            .properties(height=200)
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(trend_chart, use_container_width=True)

    with st.expander("View as table"):
        st.dataframe(
            status_df[["status", "count"]].rename(columns={"status": "Status", "count": "Tickets"}),
            hide_index=True,
            use_container_width=True,
        )
        st.dataframe(
            priority_df[["priority", "count"]].rename(columns={"priority": "Priority", "count": "Tickets"}),
            hide_index=True,
            use_container_width=True,
        )


# --- Tickets tab -------------------------------------------------------------------


def render_ticket_row(conn, t: dict):
    tid = t["ticket_id"]
    messages = db.list_messages(conn, tid)
    last_message = messages[-1]["message_text"] if messages else "No messages"

    with st.container():
        col1, col2, col3, col4, col5 = st.columns([1, 3, 2, 2, 1.6])

        with col1:
            st.markdown(f"**#{tid}**")
            st.caption(f"by {t['created_by']}")

        with col2:
            st.markdown(f"**{t['title']}**")
            assignee_line = t["assigned_to"] or "Unassigned"
            st.caption(f"{t['created_at']:%Y-%m-%d %H:%M} · Assigned to {assignee_line}")

        with col3:
            st.markdown(status_pill(t["status"]) + priority_pill(t["priority"]), unsafe_allow_html=True)

        with col4:
            st.caption(f"{last_message[:60]}{'...' if len(last_message) > 60 else ''}")

        with col5:
            b1, b2 = st.columns(2)
            with b1:
                if st.button("📝", key=f"view_{tid}", help="Details", use_container_width=True):
                    st.session_state[f"show_messages_{tid}"] = not st.session_state.get(
                        f"show_messages_{tid}", False
                    )
                    st.rerun()
            with b2:
                if st.button("🗑️", key=f"delete_{tid}", help="Delete ticket", use_container_width=True):
                    st.session_state[f"confirm_delete_{tid}"] = True
                    st.rerun()

        if st.session_state.get(f"confirm_delete_{tid}", False):
            st.warning(
                f"Delete ticket #{tid} — \"{t['title']}\"? This also deletes all of "
                "its messages. This cannot be undone."
            )
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("Yes, delete permanently", key=f"confirm_yes_{tid}", type="primary"):
                    db.delete_ticket(conn, tid)
                    st.session_state.pop(f"confirm_delete_{tid}", None)
                    st.toast("Ticket deleted.")
                    st.rerun()
            with cc2:
                if st.button("Cancel", key=f"confirm_no_{tid}"):
                    st.session_state.pop(f"confirm_delete_{tid}", None)
                    st.rerun()

        if st.session_state.get(f"show_messages_{tid}", False):
            st.markdown("---")

            st.markdown("#### ⚙️ Ticket properties")
            with st.form(f"props_form_{tid}"):
                p1, p2, p3 = st.columns(3)
                with p1:
                    new_status = st.selectbox(
                        "Status",
                        db.STATUS_OPTIONS,
                        index=db.STATUS_OPTIONS.index(t["status"]) if t["status"] in db.STATUS_OPTIONS else 0,
                        format_func=lambda s: STATUS_LABELS[s],
                        key=f"status_select_{tid}",
                    )
                with p2:
                    new_priority = st.selectbox(
                        "Priority",
                        db.PRIORITY_OPTIONS,
                        index=db.PRIORITY_OPTIONS.index(t["priority"]) if t["priority"] in db.PRIORITY_OPTIONS else 1,
                        format_func=lambda p: p.capitalize(),
                        key=f"priority_select_{tid}",
                    )
                with p3:
                    new_assignee = st.text_input(
                        "Assigned to",
                        value=t["assigned_to"] or "",
                        placeholder="Unassigned",
                        key=f"assignee_input_{tid}",
                    )
                if st.form_submit_button("Save changes", use_container_width=True):
                    db.update_ticket(
                        conn,
                        tid,
                        status=new_status,
                        priority=new_priority,
                        assigned_to=new_assignee.strip(),
                        clear_assignee=not new_assignee.strip(),
                    )
                    st.success("✓ Ticket updated")
                    st.rerun()

            st.markdown("---")
            st.markdown("### 💬 Messages")

            if messages:
                st.markdown("#### Message History")
                msg_h1, msg_h2, msg_h3, msg_h4 = st.columns([1.5, 3, 1.5, 0.8])
                with msg_h1:
                    st.markdown("**Author**")
                with msg_h2:
                    st.markdown("**Message**")
                with msg_h3:
                    st.markdown("**Date**")
                with msg_h4:
                    st.markdown("**Edit**")
                st.markdown("---")

                for m in messages:
                    if st.session_state.get(f"editing_{m['message_id']}", False):
                        with st.form(f"edit_form_{m['message_id']}"):
                            st.markdown(f"**Editing message by {m['author']}**")
                            new_text = st.text_area(
                                "Message",
                                value=m["message_text"],
                                key=f"edit_text_{m['message_id']}",
                                height=100,
                            )
                            col_save, col_cancel = st.columns(2)
                            with col_save:
                                if st.form_submit_button("✓ Save", use_container_width=True):
                                    db.update_message(conn, m["message_id"], new_text.strip())
                                    st.session_state[f"editing_{m['message_id']}"] = False
                                    st.success("Message updated")
                                    st.rerun()
                            with col_cancel:
                                if st.form_submit_button("✕ Cancel", use_container_width=True):
                                    st.session_state[f"editing_{m['message_id']}"] = False
                                    st.rerun()
                    else:
                        msg_col1, msg_col2, msg_col3, msg_col4 = st.columns([1.5, 3, 1.5, 0.8])
                        with msg_col1:
                            st.markdown(f"**{m['author']}**")
                        with msg_col2:
                            st.markdown(m["message_text"])
                        with msg_col3:
                            st.caption(f"{m['created_at']:%Y-%m-%d %H:%M}")
                        with msg_col4:
                            if st.button("✏️", key=f"edit_btn_{m['message_id']}"):
                                st.session_state[f"editing_{m['message_id']}"] = True
                                st.rerun()
                        st.markdown("")
            else:
                st.info("No messages yet. Add one below.")

            st.markdown("---")
            st.markdown("#### ➕ Add New Message")
            with st.form(f"add_msg_{tid}", clear_on_submit=True):
                new_msg = st.text_area("Message", key=f"new_msg_{tid}", placeholder="Enter your message here...")
                new_author = st.text_input("Your name / email", key=f"new_author_{tid}", placeholder="Your name")
                if st.form_submit_button("➕ Add Message", use_container_width=True):
                    if not new_msg.strip() or not new_author.strip():
                        st.warning("Message and author are required")
                    else:
                        db.add_message(conn, tid, new_msg.strip(), new_author.strip())
                        st.success("Message added")
                        st.rerun()

        st.markdown("---")


def render_tickets_tab(conn, status_filter, priority_filter, assignee_filter):
    search_ticket_id = st.text_input("🔍 Search by Ticket Number", placeholder="Enter ticket ID (e.g., 123)")

    try:
        tickets = db.list_tickets(conn, status_filter, priority_filter, assignee_filter)

        if search_ticket_id.strip():
            try:
                search_id = int(search_ticket_id.strip())
                tickets = [t for t in tickets if t["ticket_id"] == search_id]
                if not tickets:
                    st.warning(f"No ticket found with ID: {search_id}")
            except ValueError:
                st.warning("Please enter a valid ticket number.")
                tickets = []
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load tickets: {e}")
        st.stop()

    if not tickets:
        st.info("No tickets found matching your criteria.")
        return

    st.subheader(f"Tickets ({len(tickets)})")

    header_col1, header_col2, header_col3, header_col4, header_col5 = st.columns([1, 3, 2, 2, 1.6])
    with header_col1:
        st.markdown("**Ticket ID**")
    with header_col2:
        st.markdown("**Title**")
    with header_col3:
        st.markdown("**Status / Priority**")
    with header_col4:
        st.markdown("**Last Message**")
    with header_col5:
        st.markdown("**Actions**")
    st.markdown("---")

    for t in tickets:
        render_ticket_row(conn, t)


# --- App shell -----------------------------------------------------------------------


def render_app():
    conn = st.session_state.conn

    theme_name = st.session_state.get("theme_name", "Ocean Blue")
    inject_theme_css(THEMES[theme_name])

    st.markdown(
        '<div class="ticket-header"><h1>🎫 Ticket Now</h1>'
        "<p>Enterprise Service Management Platform</p></div>",
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("➕ Create New Ticket")
        with st.form("new_ticket_form", clear_on_submit=True):
            new_title = st.text_input("Title", placeholder="Brief description")
            new_created_by = st.text_input("Requester", placeholder="Your name or email")
            new_priority = st.selectbox(
                "Priority", db.PRIORITY_OPTIONS, index=1, format_func=lambda p: p.capitalize()
            )
            new_assignee = st.text_input("Assign to (optional)", placeholder="Unassigned")
            if st.form_submit_button("Create Ticket", use_container_width=True):
                if not new_title.strip() or not new_created_by.strip():
                    st.warning("Title and requester are required.")
                else:
                    db.create_ticket(
                        conn,
                        new_title.strip(),
                        new_created_by.strip(),
                        priority=new_priority,
                        assigned_to=new_assignee.strip() or None,
                    )
                    st.success("✓ Ticket created!")
                    st.rerun()

        st.divider()
        st.header("🔍 Filters")
        status_filter = st.selectbox("Status", ["All"] + db.STATUS_OPTIONS)
        priority_filter = st.selectbox(
            "Priority", ["All"] + db.PRIORITY_OPTIONS, format_func=lambda p: p if p == "All" else p.capitalize()
        )
        assignees = db.list_distinct_assignees(conn)
        assignee_filter = st.selectbox("Assignee", ["All"] + assignees)

        st.divider()
        st.header("🎨 Theme")
        st.selectbox("Color theme", list(THEMES.keys()), key="theme_name")

        st.divider()
        st.caption(f"Connected to **{conn['host']}**")
        if st.button("Disconnect"):
            st.session_state.conn = None
            st.rerun()

        st.divider()
        st.caption("Ticket Now v2.0")

    tab_dashboard, tab_tickets = st.tabs(["📊 Dashboard", "🎫 Tickets"])
    with tab_dashboard:
        render_dashboard(conn)
    with tab_tickets:
        render_tickets_tab(conn, status_filter, priority_filter, assignee_filter)


# Auto-connect on first load
if st.session_state.conn is None:
    initialize_connection()

if st.session_state.conn is not None:
    render_app()
