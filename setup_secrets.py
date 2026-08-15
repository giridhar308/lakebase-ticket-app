#!/usr/bin/env python3
"""
Setup script to create Databricks secret scope and add Lakebase credentials.

Usage:
    python setup_secrets.py
    
The script will prompt you interactively for all required credentials.
"""

import getpass
import sys

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.workspace import ScopeBackendType


SECRET_SCOPE = "lakebase-app"


def create_scope_if_not_exists(w: WorkspaceClient):
    """Create the secret scope if it doesn't already exist."""
    try:
        # Try to get the scope
        scopes = list(w.secrets.list_scopes())
        if any(s.name == SECRET_SCOPE for s in scopes):
            print(f"✓ Secret scope '{SECRET_SCOPE}' already exists")
            return
    except Exception:
        pass  # Scope doesn't exist, will create it
    
    try:
        w.secrets.create_scope(
            scope=SECRET_SCOPE,
            scope_backend_type=ScopeBackendType.DATABRICKS
        )
        print(f"✓ Created secret scope '{SECRET_SCOPE}'")
    except Exception as e:
        print(f"✗ Failed to create secret scope: {e}")
        sys.exit(1)


def put_secret(w: WorkspaceClient, key: str, value: str):
    """Add or update a secret in the scope."""
    try:
        w.secrets.put_secret(
            scope=SECRET_SCOPE,
            key=key,
            string_value=value
        )
        print(f"✓ Added secret '{key}'")
    except Exception as e:
        print(f"✗ Failed to add secret '{key}': {e}")
        sys.exit(1)


def grant_app_permissions(w: WorkspaceClient, app_name: str):
    """Grant the Databricks App permission to read secrets."""
    try:
        # Get the app details to find its service principal
        from databricks.sdk.service.apps import App
        
        print(f"\nGranting secret access to app '{app_name}'...")
        
        # List all apps to find the service principal
        apps = list(w.apps.list())
        target_app = None
        for app in apps:
            if app.name == app_name:
                target_app = app
                break
        
        if not target_app:
            print(f"⚠ Warning: App '{app_name}' not found. Please grant permissions manually.")
            print(f"  Run: databricks secrets put-acl --scope {SECRET_SCOPE} --principal <app-service-principal-id> --permission READ")
            return
        
        # Grant READ permission to the app's service principal
        if target_app.service_principal_id:
            w.secrets.put_acl(
                scope=SECRET_SCOPE,
                principal=target_app.service_principal_id,
                permission="READ"
            )
            print(f"✓ Granted READ permission to service principal: {target_app.service_principal_id}")
        else:
            print(f"⚠ Warning: Could not find service principal for app '{app_name}'")
            print(f"  Grant permissions manually if needed.")
    except Exception as e:
        print(f"⚠ Warning: Could not automatically grant app permissions: {e}")
        print(f"\nTo grant permissions manually, run:")
        print(f"  databricks secrets put-acl --scope {SECRET_SCOPE} --principal <app-service-principal-id> --permission READ")


def get_credentials_interactive():
    """Prompt user for credentials interactively."""
    print("\nEnter Lakebase connection details:")
    print("Format: postgresql://username:password@host:port/database")
    print("Example: postgresql://myuser:mypass@ep-xxx.database.us-west-2.cloud.databricks.com:5432/databricks_postgres")
    print("Note: Tables will always be created in the 'public' schema.\n")
    
    connection_string = input("Lakebase connection string (required): ").strip()
    if not connection_string:
        print("Error: Connection string is required")
        sys.exit(1)
    
    # Validate connection string format
    if not connection_string.startswith(("postgresql://", "postgres://")):
        print("Error: Connection string must start with postgresql:// or postgres://")
        sys.exit(1)
    
    return {
        "connection_string": connection_string,
    }


def main():
    print("=" * 60)
    print("Lakebase Ticket App - Secret Setup")
    print("=" * 60)
    
    # Get credentials interactively
    creds = get_credentials_interactive()
    
    print(f"\nSetting up secret scope '{SECRET_SCOPE}'...\n")
    
    # Initialize Databricks SDK client
    w = WorkspaceClient()
    
    # Create scope
    create_scope_if_not_exists(w)
    
    # Add required secrets
    print("\nAdding secrets...")
    put_secret(w, "lakebase_connection_string", creds["connection_string"])
    
    # Grant app permissions
    app_name = input("\nDatabricks App name (for granting secret access) [lakebase-ticket-app]: ").strip() or "lakebase-ticket-app"
    grant_app_permissions(w, app_name)
    
    print(f"\n✓ Setup complete! Secret scope '{SECRET_SCOPE}' is ready.")
    print("\nYou can now deploy the app with:")
    print("  databricks apps create lakebase-ticket-app --source-code-path .")
    print("  databricks apps start lakebase-ticket-app")
    print("\nIf you already deployed the app, restart it to use the new secrets:")
    print("  databricks apps restart lakebase-ticket-app")


if __name__ == "__main__":
    main()
