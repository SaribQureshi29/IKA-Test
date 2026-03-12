"""
Admin panel: login, session tokens, Entra user provisioning, SharePoint whitelist/blacklist, add admin.
All admin routes and helpers live here; main app includes this router.
"""
import os
import sys
import time
import hmac
import hashlib
import logging
import threading  # ADDED: for background thread
import requests
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from heapq import nlargest
from graph_Service import list_user_sharepoint_files
import base64
import requests
from sql_db import log_to_system
import logging
from azure.search.documents.models import VectorizedQuery
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from azure.identity import ClientSecretCredential, get_bearer_token_provider
import re
from openai import AzureOpenAI
from typing import Optional, List, Dict, Any
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pyodbc
from config import get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET

from sql_db import (
    validate_admin,
    create_admin_session,
    get_admin_username_for_token,
    get_admin_username_if_exists,
    add_admin_user,
    list_all_admin_usernames,
    remove_admin_user,
    get_sharepoint_whitelist,
    get_sharepoint_blacklist,
    add_sharepoint_whitelist,
    remove_sharepoint_whitelist,
    add_sharepoint_blacklist,
    remove_sharepoint_blacklist,
    get_config_value,
    log_admin_action,
)
from graph_Service import get_app_only_token, check_entra_user_exists, search_entra_users
from auth import verify_token


logger = logging.getLogger("system_logger")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False

router = APIRouter(prefix="/admin", tags=["admin"])

# --- Hardcoded admin (single account, always works) ---
# _ADMIN_EMAIL() = get_secret("ADMINEMAIL")
# _ADMIN_PASSWORD = get_secret("ADMINPASSWORD")
def _ADMIN_EMAIL():
    return get_secret("ADMINEMAIL")
def _ADMIN_PASSWORD():
    return get_secret("ADMINPASSWORD")
_ADMIN_SESSION_SECRET = ("ika_admin_session_secret").encode("utf-8")


def _make_hardcoded_admin_token() -> str:
    """Issue a signed token for hardcoded admin (no DB). Valid 24h."""
    expiry = int(time.time()) + 24 * 3600
    payload = f"{_ADMIN_EMAIL()}:{expiry}"
    sig = hmac.new(_ADMIN_SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ika_admin:{expiry}:{sig}"


def _is_valid_hardcoded_admin_token(token: str) -> bool:
    """Verify signed token for hardcoded admin."""
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "ika_admin":
        return False
    try:
        expiry = int(parts[1])
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    payload = f"{_ADMIN_EMAIL()}:{expiry}"
    expected = hmac.new(_ADMIN_SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts[2])


def _require_admin_token(x_admin_token: str = Header(None, alias="X-Admin-Token")):
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="Admin token required")
    if _is_valid_hardcoded_admin_token(x_admin_token):
        return _ADMIN_EMAIL()
    username = get_admin_username_for_token(x_admin_token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired admin token")
    return username


# --- Pydantic models ---
class AdminLoginRequest(BaseModel):
    username: str
    password: str


class SiteUrlRequest(BaseModel):
    url: str


class AddAdminRequest(BaseModel):
    username: str
    password: str | None = None  # If omitted, user must exist in Azure AD (email-only add).


# ==================== FIXED FUNCTION WITH BETTER ERROR HANDLING ====================
import os
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

def _trigger_ingest():
    """Call Azure Function to run FULL ingest after whitelist/blacklist change."""
    print("=== _trigger_ingest() STARTED ===")
    
    try:
        # Get secrets from Key Vault directly (works both locally and in Azure)
        # KEY_VAULT_URL = "https://ikakv.vault.azure.net/"
        
        # Use DefaultAzureCredential - works with:
        # - Your Azure CLI login (local)
        # - Managed Identity (Azure)
        # SQL_SERVER = "ika-chat-sql-server.database.windows.net"
        # SQL_DATABASE = "ika-chat-db"
        # SQL_USERNAME = "SqlAdmin"
        # SQL_PASSWORD = "Admin_456!!"
        # SQL_DRIVER = "{ODBC Driver 18 for SQL Server}"
        # KEY_VAULT_URL = "https://ikakv.vault.azure.net/"

        # def get_values_from_sql():
        #     conn_str = f'DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};PORT=1433;DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD={SQL_PASSWORD}'
        #     try:
        #         with pyodbc.connect(conn_str) as conn:
        #             with conn.cursor() as cursor:
        #                 cursor.execute("""
        #                     SELECT ConfigKey, ConfigValue 
        #                     FROM Configs 
        #                     WHERE ConfigKey IN ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 'SP_SITE_PATH', 'SP_EXCLUDE_PATHS', 'MODEL_DEPLOYMENT')
        #                 """)
        #                 rows = cursor.fetchall()
        #                 config_dict = {row.ConfigKey: row.ConfigValue for row in rows}
        #                 return (
        #                     config_dict.get('TENANT_ID', ""),
        #                     config_dict.get('CLIENT_ID', ""),
        #                     config_dict.get('CLIENT_SECRET', ""),
        #                     config_dict.get('SP_SITE_PATH', ""),
        #                     config_dict.get('SP_EXCLUDE_PATHS', ""),
        #                     config_dict.get('MODEL_DEPLOYMENT', "")
        #                 )
        #     except Exception as e:
        #         print(f"[SQL ERROR] Could not fetch configs in utils.py: {e}")
        #         return "", "", "", "", ""

        # (TENANT_ID,
        # CLIENT_ID,
        # CLIENT_SECRET,
        # SP_SITE_PATH,
        # SP_EXCLUDE_PATHS,
        # MODEL_DEPLOYMENT) = get_values_from_sql()

        # credential = ClientSecretCredential(
        #     tenant_id=TENANT_ID,
        #     client_id=CLIENT_ID,
        #     client_secret=CLIENT_SECRET
        # )
        # kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

        # def get_secret(name: str) -> str:
        #     return kv_client.get_secret(name).value
        # Get the secrets from Key Vault
        ingest_secret = get_secret("INGEST-TRIGGER-SECRET")
        function_url = get_secret("INGEST-TRIGGER-URL")
        
        print(f"✅ Successfully retrieved secrets from Key Vault")
        print(f"🔍 URL: {function_url}")
        
        def call_function():
            try:
                print("📤 Sending request...")
                response = requests.post(
                    function_url,
                    headers={
                        "X-Ingest-Secret": ingest_secret,
                        "Content-Type": "application/json"
                    },
                    json={"trigger": "whitelist_blacklist_change"},
                    timeout=10
                )
                print(f"📥 Response status: {response.status_code}")
                
                if response.status_code == 200:
                    logger.info("✅ FULL ingest triggered successfully on Azure")
                else:
                    logger.error(f"❌ Failed to trigger ingest: HTTP {response.status_code}")
                    
            except Exception as e:
                logger.error(f"❌ Error in thread: {e}")
        
        import threading
        thread = threading.Thread(target=call_function)
        thread.daemon = True
        thread.start()
        print("✅ Thread started")
        
    except Exception as e:
        print(f"❌ Error accessing Key Vault: {e}")
        logger.error(f"❌ Failed to get secrets from Key Vault: {e}")
    
    print("=== _trigger_ingest() COMPLETED ===")

        
        
# =========================================================


def _get_organization_domain(access_token: str) -> str:
    """Get default verified domain from Microsoft Graph when ORGANIZATION_DOMAIN not in env."""
    try:
        org_resp = requests.get(
            "https://graph.microsoft.com/v1.0/organization",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=10,
        )
        if org_resp.status_code != 200:
            return ""
        verified = (org_resp.json().get("value") or [{}])[0].get("verifiedDomains") or []
        first = ""
        for vd in verified:
            if vd.get("name"):
                name = (vd.get("name") or "").strip()
                if vd.get("isDefault"):
                    return name
                if not first:
                    first = name
        return first
    except Exception as e:
        logger.warning(f"Could not get organization domain from Graph: {e}")
    return ""


# --- Routes ---

@router.get("")
async def admin_page():
    path = os.path.join("static", "admin.html") if os.path.isdir("static") else None
    if path and os.path.exists(path):
        return FileResponse(path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Admin panel not found")


def _username_candidates_from_payload(payload: dict) -> list:
    """Collect all possible username/identity values from Azure AD JWT (different tenants use different claims)."""
    candidates = []
    for key in ("preferred_username", "upn", "email", "unique_name", "name"):
        val = (payload.get(key) or "").strip()
        if val and val not in candidates:
            candidates.append(val)
    return candidates


@router.get("/check-access")
def admin_check_access(authorization: str = Header(default=None)):
    """
    Check if the logged-in user (Azure AD Bearer token) has admin access.
    Returns { hasAccess: bool, token?: str }. If hasAccess, token is an admin session token (no need to login again on admin panel).
    """
    if not authorization or not authorization.strip().lower().startswith("bearer "):
        return {"hasAccess": False}
    try:
        payload, _ = verify_token(authorization)
    except Exception:
        return {"hasAccess": False}
    candidates = _username_candidates_from_payload(payload)
    for username in candidates:
        if not username:
            continue
        normalized_username = username.strip().lower()
        if normalized_username == _ADMIN_EMAIL().lower():
            return {"hasAccess": True, "token": _make_hardcoded_admin_token()}
        db_username = get_admin_username_if_exists(normalized_username)
        if db_username:
            token = create_admin_session(db_username)
            if token:
                return {"hasAccess": True, "token": token}
    return {"hasAccess": False}
 

@router.post("/login")
def admin_login(req: AdminLoginRequest):
    username = (req.username or "").strip()
    password = (req.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")
    if username == _ADMIN_EMAIL() and password == _ADMIN_PASSWORD():
        log_admin_action(username, "LOGIN", None)
        return {"ok": True, "token": _make_hardcoded_admin_token()}
    if not validate_admin(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_admin_session(username)
    if not token:
        raise HTTPException(status_code=500, detail="Failed to create session")
    log_admin_action(username, "LOGIN", None)
    return {"ok": True, "token": token}


@router.get("/check-entra-permission")
def admin_check_entra_permission(admin_user: str = Header(None, alias="X-Admin-Token")):
    """Verify the API app has User.ReadWrite.All."""
    _require_admin_token(admin_user)
    access_token = get_app_only_token()
    if not access_token:
        return {"ok": False, "message": "Failed to get Graph token (API app from Key Vault)."}
    check_resp = requests.get(
        "https://graph.microsoft.com/v1.0/users?$top=1",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        timeout=10,
    )
    if check_resp.status_code == 200:
        return {"ok": True, "message": "User.ReadWrite.All access verified (API app). You can provision users."}
    if check_resp.status_code == 403:
        return {
            "ok": False,
            "message": "User.ReadWrite.All is missing or admin consent not granted for the API app. In Azure: that app registration → API permissions → Microsoft Graph → Application → User.ReadWrite.All → Grant admin consent."
        }
    return {"ok": False, "message": f"Graph returned {check_resp.status_code}. Check API app permissions."}

import logging
import sys

# Make sure your logger has the correct handler and level
logger = logging.getLogger("system_logger")
logger.setLevel(logging.INFO)  # Ensure it's set to INFO

# Add a handler that prints to console if not already present
if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(console_handler)
    
# Also add a handler for the root logger to see all logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


@router.get("/whitelist")
def admin_get_whitelist(admin_user: str = Header(None, alias="X-Admin-Token")):
    _require_admin_token(admin_user)
    return {"sites": get_sharepoint_whitelist()}


@router.post("/whitelist")
def admin_add_whitelist(body: SiteUrlRequest, admin_user: str = Header(None, alias="X-Admin-Token")):
    admin_username = _require_admin_token(admin_user)
    print(f"ROUTE HIT: admin_add_whitelist by {admin_username}")
    logger.info(f"WHITELIST ADD - User: {admin_username}, URL: {body.url}")
    
    if not add_sharepoint_whitelist(body.url):
        print(f"WHITELIST ADD FAILED: {body.url}")
        logger.error(f"WHITELIST ADD FAILED - Invalid URL: {body.url}")
        raise HTTPException(status_code=400, detail="Invalid URL or add failed")
    
    log_admin_action(admin_username, "ADD_WHITELIST", body.url)
    print(f"CALLING _trigger_ingest()...")
    logger.info(f"WHITELIST ADD SUCCESS - Calling ingest trigger...")
    
    _trigger_ingest()
    
    print(f"_trigger_ingest() completed")
    logger.info(f"WHITELIST ADD COMPLETE - Returning to admin panel")
    return {"ok": True, "sites": get_sharepoint_whitelist()}


@router.delete("/whitelist")
def admin_remove_whitelist(url: str = Query(..., description="Site URL to remove"), admin_user: str = Header(None, alias="X-Admin-Token")):
    admin_username = _require_admin_token(admin_user)
    print(f"ROUTE HIT: admin_remove_whitelist by {admin_username}")
    logger.info(f"WHITELIST REMOVE - User: {admin_username}, URL: {url}")
    
    remove_sharepoint_whitelist(url)
    log_admin_action(admin_username, "REMOVE_WHITELIST", url)
    print(f"CALLING _trigger_ingest()...")
    logger.info(f"WHITELIST REMOVE SUCCESS - Calling ingest trigger...")
    
    _trigger_ingest()
    
    print(f"_trigger_ingest() completed")
    logger.info(f"WHITELIST REMOVE COMPLETE - Returning to admin panel")
    return {"ok": True, "sites": get_sharepoint_whitelist()}


@router.get("/blacklist")
def admin_get_blacklist(admin_user: str = Header(None, alias="X-Admin-Token")):
    _require_admin_token(admin_user)
    return {"sites": get_sharepoint_blacklist()}


@router.post("/blacklist")
def admin_add_blacklist(body: SiteUrlRequest, admin_user: str = Header(None, alias="X-Admin-Token")):
    admin_username = _require_admin_token(admin_user)
    print(f"ROUTE HIT: admin_add_blacklist by {admin_username}")
    logger.info(f"BLACKLIST ADD - User: {admin_username}, URL: {body.url}")
    
    if not add_sharepoint_blacklist(body.url):
        print(f"BLACKLIST ADD FAILED: {body.url}")
        logger.error(f"BLACKLIST ADD FAILED - Invalid URL: {body.url}")
        raise HTTPException(status_code=400, detail="Invalid URL or add failed")
    
    log_admin_action(admin_username, "ADD_BLACKLIST", body.url)
    print(f"CALLING _trigger_ingest()...")
    logger.info(f"BLACKLIST ADD SUCCESS - Calling ingest trigger...")
    
    _trigger_ingest()
    
    print(f"_trigger_ingest() completed")
    logger.info(f"BLACKLIST ADD COMPLETE - Returning to admin panel")
    return {"ok": True, "sites": get_sharepoint_blacklist()}


@router.delete("/blacklist")
def admin_remove_blacklist(url: str = Query(..., description="Site URL to remove"), admin_user: str = Header(None, alias="X-Admin-Token")):
    admin_username = _require_admin_token(admin_user)
    print(f"ROUTE HIT: admin_remove_blacklist by {admin_username}")
    logger.info(f"BLACKLIST REMOVE - User: {admin_username}, URL: {url}")
    
    remove_sharepoint_blacklist(url)
    log_admin_action(admin_username, "REMOVE_BLACKLIST", url)
    print(f"CALLING _trigger_ingest()...")
    logger.info(f"BLACKLIST REMOVE SUCCESS - Calling ingest trigger...")
    
    _trigger_ingest()
    
    print(f"_trigger_ingest() completed")
    logger.info(f"BLACKLIST REMOVE COMPLETE - Returning to admin panel")
    return {"ok": True, "sites": get_sharepoint_blacklist()}


@router.get("/search-users")
def admin_search_users(q: str = Query("", description="Search by email or UPN prefix"), admin_user: str = Header(None, alias="X-Admin-Token")):
    """Return Azure AD users whose mail or userPrincipalName starts with the query (for Add admin autocomplete)."""
    _require_admin_token(admin_user)
    query = (q or "").strip()
    if len(query) < 2:
        return {"users": []}
    users = search_entra_users(query, top=10)
    return {"users": [{"email": u["email"], "displayName": u["displayName"]} for u in users]}


@router.post("/add-admin")
def admin_add_admin(req: AddAdminRequest, x_admin_token: str = Header(None, alias="X-Admin-Token")):
    """Add an existing user as admin by email. Email is validated against Azure AD; no password needed (they sign in via Azure)."""
    admin_username = _require_admin_token(x_admin_token)
    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Email required")
    if "@" not in username:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if get_admin_username_if_exists(username):
        raise HTTPException(status_code=400, detail="This user is already an admin.")
    if not check_entra_user_exists(username):
        raise HTTPException(status_code=400, detail="User not found in Azure AD. Enter the exact email of an existing user.")
    _, err = add_admin_user(username, None)
    if err:
        raise HTTPException(status_code=400, detail=err)
    log_admin_action(admin_username, "ADD_ADMIN", username)
    return {"ok": True, "message": f"Admin added: {username}"}


@router.get("/admins")
def admin_list_admins(admin_user: str = Header(None, alias="X-Admin-Token")):
    """List all admin usernames. Any logged-in admin can view. currentUsername is used by frontend to show Remove only to super admin."""
    current_username = _require_admin_token(admin_user)
    is_super_admin = current_username.lower() == _ADMIN_EMAIL().lower()
    return {"admins": list_all_admin_usernames(), "currentUsername": current_username,"isSuperAdmin": is_super_admin,
        "superAdminEmail": _ADMIN_EMAIL()}


@router.delete("/admins")
def admin_remove_admin(
    username: str = Query(..., description="Admin username to remove"),
    x_admin_token: str = Header(None, alias="X-Admin-Token"),
):
    """Remove an admin from the DB. Only super admin (IKA admin) can remove. Removed admin loses access immediately."""
    admin_username = _require_admin_token(x_admin_token)
    if admin_username.lower() != _ADMIN_EMAIL().lower():
        raise HTTPException(status_code=403, detail="Only super admin can remove admins")
    ok, err = remove_admin_user(username)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "Remove failed")
    log_admin_action(admin_username, "REMOVE_ADMIN", username)
    return {"ok": True, "admins": list_all_admin_usernames()}

