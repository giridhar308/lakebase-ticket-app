# Ticket Now

An enterprise service management platform inspired by ServiceNow, built with Streamlit and backed by Lakebase (Databricks Postgres).

## Features

* 🔍 **Search tickets by ID** - Quickly find specific tickets
* 📊 **Tabular view** - Clean table layout with ticket ID, title, status, and messages
* ✏️ **Inline editing** - Update ticket status and message content directly in the UI
* 💬 **Message history** - View and edit all messages for each ticket
* 🎨 **Modern UI** - ServiceNow-inspired gradient design with clean layouts
* 🔒 **Secure** - Database credentials stored in Databricks secrets

## Setup

### Option 1: Automated Setup (Recommended)

Use the provided setup script to create the secret scope and add credentials:

```bash
python setup_secrets.py
```

The script will prompt you for your **Lakebase connection string**:
* Format: `postgresql://username:password@host:port/database`
* Example: `postgresql://student:pass@ep-xxx.database.us-east-2.cloud.databricks.com:5432/databricks_postgres`

**Note:** All tables are created in the `public` schema.

### Option 2: Manual Setup via Databricks CLI

This app reads Lakebase credentials from a Databricks secret scope named `lakebase-app`.

#### 1. Create the Secret Scope

```bash
databricks secrets create-scope lakebase-app
```

#### 2. Add Required Secret

```bash
# Lakebase connection string
# Format: postgresql://username:password@host:port/database
databricks secrets put-secret lakebase-app lakebase_connection_string
```

**Note:** Tables are always created in the `public` schema.

### 3. Deploy the App

Deploy as a Databricks App:

```bash
databricks apps create lakebase-ticket-app --source-code-path .
```

Or start the app:

```bash
databricks apps start lakebase-ticket-app
```

## How It Works

- The app automatically connects to Lakebase on startup using a connection string from the `lakebase-app` secret scope
- The connection string contains all authentication details (username, password, host, port, database)
- Credentials are securely stored in Databricks secrets and never exposed in the code or UI

## Troubleshooting

### Permission Error: "does not have secret-scopes.secrets/get permission"

If you get this error when running the app, it means the app's service principal doesn't have permission to read the secrets.

**Quick Fix:**

Run the fix script:

```bash
python fix_permissions.py
```

Then restart your app:

```bash
databricks apps restart lakebase-ticket-app
```

**Manual Fix:**

Grant the app's service principal READ permission to the secret scope:

```bash
databricks secrets put-acl --scope lakebase-app --principal <service-principal-id> --permission READ
```

You can find the service principal ID in the error message or by listing your apps.

## Ticket Management

* Create and manage support tickets
* Update ticket status (open, in_progress, resolved, closed) with inline dropdowns
* Add and edit messages/comments on tickets
* Filter tickets by status
* Search for specific tickets by ID
* Persistent storage in Lakebase Postgres public schema
