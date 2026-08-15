"""
Ticket Now — Enterprise Service Management Platform

A ServiceNow-inspired ticket management system built as a Databricks App,
backed by Lakebase (Postgres). Features include:
- Search tickets by ID
- Inline status updates
- Message editing and history
- Clean, modern UI

Connection details are securely stored in Databricks secrets.
"""

import streamlit as st
from databricks.sdk.runtime import dbutils

import db

st.set_page_config(page_title="Ticket Now", page_icon="🎫", layout="wide")

if "conn" not in st.session_state:
    st.session_state.conn = None


def get_connection_from_secrets():
    """Load Lakebase connection details from Databricks secrets.
    
    Expected secrets in scope 'lakebase-app':
    - lakebase_connection_string: Full Postgres connection string
      (postgresql://user:password@host:port/database)
    
    Note: Tables are always created in the 'public' schema.
    """
    secret_scope = "lakebase-app"
    
    try:
        connection_string = dbutils.secrets.get(scope=secret_scope, key="lakebase_connection_string")
        
        # Parse the connection string to extract components
        conn = db.parse_lakebase_url(connection_string)
        
        # Always use public schema (hardcoded in db.py)
        conn["schema"] = "public"
        
        # Default SSL mode if not in connection string
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


def render_app():
    conn = st.session_state.conn

    # Custom CSS for ServiceNow-like styling
    st.markdown("""
        <style>
        .ticket-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 2rem;
            border-radius: 10px;
            color: white;
            margin-bottom: 2rem;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .stButton>button {
            border-radius: 5px;
        }
        </style>
    """, unsafe_allow_html=True)

    # Header
    st.markdown('<div class="ticket-header"><h1>🎫 Ticket Now</h1><p>Enterprise Service Management Platform</p></div>', unsafe_allow_html=True)

    with st.sidebar:
        st.header("➕ Create New Ticket")
        with st.form("new_ticket_form", clear_on_submit=True):
            new_title = st.text_input("Title", placeholder="Brief description")
            new_created_by = st.text_input("Requester", placeholder="Your name or email")
            if st.form_submit_button("Create Ticket", use_container_width=True):
                if not new_title.strip() or not new_created_by.strip():
                    st.warning("Title and requester are required.")
                else:
                    db.create_ticket(conn, new_title.strip(), new_created_by.strip())
                    st.success("✓ Ticket created!")
                    st.rerun()

        st.divider()
        st.header("🔍 Filters")
        status_filter = st.selectbox("Status", ["All"] + db.STATUS_OPTIONS, label_visibility="collapsed")
        
        st.divider()
        st.caption("Ticket Now v1.0")

    # Search bar
    search_ticket_id = st.text_input("🔍 Search by Ticket Number", placeholder="Enter ticket ID (e.g., 123)")

    try:
        tickets = db.list_tickets(conn, status_filter)
        
        # Filter by ticket ID if search is active
        if search_ticket_id.strip():
            try:
                search_id = int(search_ticket_id.strip())
                tickets = [t for t in tickets if t['ticket_id'] == search_id]
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
        st.stop()

    # Display tickets table header
    st.subheader(f"Tickets ({len(tickets)})")
    
    # Table header
    header_col1, header_col2, header_col3, header_col4, header_col5 = st.columns([1, 3, 2, 2, 1.5])
    with header_col1:
        st.markdown("**Ticket ID**")
    with header_col2:
        st.markdown("**Title**")
    with header_col3:
        st.markdown("**Status**")
    with header_col4:
        st.markdown("**Last Message**")
    with header_col5:
        st.markdown("**Actions**")
    
    st.markdown("---")
    
    for t in tickets:
        # Get messages for this ticket
        messages = db.list_messages(conn, t["ticket_id"])
        last_message = messages[-1]["message_text"] if messages else "No messages"
        
        with st.container():
            # Row with ticket info
            col1, col2, col3, col4, col5 = st.columns([1, 3, 2, 2, 1.5])
            
            with col1:
                st.markdown(f"**#{t['ticket_id']}**")
                st.caption(f"by {t['created_by']}")
            
            with col2:
                st.markdown(f"**{t['title']}**")
                st.caption(f"{t['created_at']:%Y-%m-%d %H:%M}")
            
            with col3:
                current_index = (
                    db.STATUS_OPTIONS.index(t["status"])
                    if t["status"] in db.STATUS_OPTIONS
                    else 0
                )
                new_status = st.selectbox(
                    "Status",
                    db.STATUS_OPTIONS,
                    index=current_index,
                    key=f"status_{t['ticket_id']}",
                    label_visibility="collapsed"
                )
                if new_status != t["status"]:
                    db.update_ticket_status(conn, t["ticket_id"], new_status)
                    st.success("✓ Updated")
                    st.rerun()
            
            with col4:
                st.caption(f"{last_message[:60]}{'...' if len(last_message) > 60 else ''}")
            
            with col5:
                if st.button("📝 Details", key=f"view_{t['ticket_id']}", use_container_width=True):
                    st.session_state[f"show_messages_{t['ticket_id']}"] = \
                        not st.session_state.get(f"show_messages_{t['ticket_id']}", False)
                    st.rerun()
            
            # Messages section (expandable)
            if st.session_state.get(f"show_messages_{t['ticket_id']}", False):
                st.markdown("---")
                st.markdown("### 💬 Messages")
                
                if messages:
                    # Display messages in a clean table format
                    st.markdown("#### Message History")
                    
                    # Table header for messages
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
                            # Edit mode
                            with st.form(f"edit_form_{m['message_id']}"):
                                st.markdown(f"**Editing message by {m['author']}**")
                                new_text = st.text_area(
                                    "Message",
                                    value=m["message_text"],
                                    key=f"edit_text_{m['message_id']}",
                                    height=100
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
                            # Display mode
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
                
                # Add new message form
                st.markdown("#### ➕ Add New Message")
                with st.form(f"add_msg_{t['ticket_id']}", clear_on_submit=True):
                    new_msg = st.text_area("Message", key=f"new_msg_{t['ticket_id']}", placeholder="Enter your message here...")
                    new_author = st.text_input("Your name / email", key=f"new_author_{t['ticket_id']}", placeholder="Your name")
                    if st.form_submit_button("➕ Add Message", use_container_width=True):
                        if not new_msg.strip() or not new_author.strip():
                            st.warning("Message and author are required")
                        else:
                            db.add_message(conn, t["ticket_id"], new_msg.strip(), new_author.strip())
                            st.success("Message added")
                            st.rerun()
            
            st.markdown("---")


# Auto-connect on first load
if st.session_state.conn is None:
    initialize_connection()

if st.session_state.conn is not None:
    render_app()
