#!/usr/bin/env python3
"""
Quick fix script to grant Databricks App permission to read secrets.

This fixes the error:
"User <id> does not have secret-scopes.secrets/get permission on scope lakebase-app"
"""

from databricks.sdk import WorkspaceClient

SECRET_SCOPE = "lakebase-app"

def main():
    print("=" * 60)
    print("Fix App Secret Permissions")
    print("=" * 60)
    
    app_name = input("\nEnter your Databricks App name [lakebase-ticket-app]: ").strip() or "lakebase-ticket-app"
    
    w = WorkspaceClient()
    
    print(f"\nLooking up app '{app_name}'...")
    
    try:
        # Find the app and its service principal
        apps = list(w.apps.list())
        target_app = None
        for app in apps:
            if app.name == app_name:
                target_app = app
                break
        
        if not target_app:
            print(f"✗ App '{app_name}' not found.")
            print(f"\nAvailable apps:")
            for app in apps:
                print(f"  - {app.name}")
            return
        
        print(f"✓ Found app: {app_name}")
        
        if not target_app.service_principal_id:
            print(f"✗ Could not find service principal ID for app '{app_name}'")
            return
        
        print(f"  Service Principal ID: {target_app.service_principal_id}")
        
        # Grant READ permission
        print(f"\nGranting READ permission to secret scope '{SECRET_SCOPE}'...")
        w.secrets.put_acl(
            scope=SECRET_SCOPE,
            principal=target_app.service_principal_id,
            permission="READ"
        )
        
        print(f"✓ Successfully granted READ permission!")
        print(f"\nRestart your app to apply the changes:")
        print(f"  databricks apps restart {app_name}")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        print(f"\nTo grant permissions manually, run:")
        print(f"  databricks secrets put-acl --scope {SECRET_SCOPE} --principal <service-principal-id> --permission READ")

if __name__ == "__main__":
    main()
