import os
import requests
import urllib.parse
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pyodbc
from config import (get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET,
                    SP_SITE_PATH, SP_EXCLUDE_PATHS)

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
#                     WHERE ConfigKey IN ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 'SP_SITE_PATH', 'SP_EXCLUDE_PATHS')
#                 """)
#                 rows = cursor.fetchall()
#                 config_dict = {row.ConfigKey: row.ConfigValue for row in rows}
#                 return (
#                     config_dict.get('TENANT_ID', ""),
#                     config_dict.get('CLIENT_ID', ""),
#                     config_dict.get('CLIENT_SECRET', ""),
#                     config_dict.get('SP_SITE_PATH', ""),
#                     config_dict.get('SP_EXCLUDE_PATHS', "")
#                 )
#     except Exception as e:
#         print(f"[SQL ERROR] Could not fetch configs in graph_Service.py: {e}")
#         return "", "", "", "", ""

# (TENANT_ID,
#  CLIENT_ID,
#  CLIENT_SECRET,
#  SP_SITE_PATH,
#  SP_EXCLUDE_PATHS) = get_values_from_sql()

# print(SP_SITE_PATH)

# credential = ClientSecretCredential(
#     tenant_id=TENANT_ID,
#     client_id=CLIENT_ID,
#     client_secret=CLIENT_SECRET
# )

# kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

# def get_secret(name: str) -> str:
#     return kv_client.get_secret(name).value

API_CLIENT_ID = get_secret("API-CLIENT-ID") #KV
API_CLIENT_SECRET = get_secret("API-CLIENT-SECRET") #KV

TOKEN_ENDPOINT = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
GRAPH_SEARCH_URL = "https://graph.microsoft.com/v1.0/search/query"
GRAPH_USERS_URL = "https://graph.microsoft.com/v1.0/users"

def normalize_path(url: str) -> str:
    """Lowercase, remove trailing slashes, decode %20 to space"""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    url = url.replace("%20", " ")
    return url.lower()

def get_app_only_token() -> str | None:
    """Get application-only token for Microsoft Graph (no user context). Uses API app (Key Vault) which has User.ReadWrite.All."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": API_CLIENT_ID,
        "client_secret": API_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
    }
    resp = requests.post(TOKEN_ENDPOINT, headers=headers, data=data)
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


def check_entra_user_exists(email_or_upn: str) -> bool:
    """Return True if a user with the given email/userPrincipalName exists in Entra ID. Uses app-only token (User.Read.All or User.ReadWrite.All)."""
    upn = (email_or_upn or "").strip()
    if not upn or "@" not in upn:
        return False
    token = get_app_only_token()
    if not token:
        return False
    encoded = urllib.parse.quote(upn, safe="")
    url = f"{GRAPH_USERS_URL}/{encoded}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def search_entra_users(query: str, top: int = 10) -> list[dict]:
    """Search Entra ID users by displayName (tokenized, so 'khan' matches 'Usman Khan'), mail or userPrincipalName (startswith). Returns list of {email, displayName}. Uses app-only token."""
    q = (query or "").strip()
    if not q or len(q) < 2:
        return []
    token = get_app_only_token()
    if not token:
        return []
    # $search: escape backslash and double-quote in the search term; displayName is tokenized (e.g. "khan" matches "Usman Khan"), mail/upn use startswith
    safe = q.replace("\\", "\\\\").replace('"', '\\"')
    search_clauses = f'"displayName:{safe}" OR "mail:{safe}" OR "userPrincipalName:{safe}"'
    params = {
        "$search": search_clauses,
        "$select": "userPrincipalName,mail,displayName",
        "$top": min(max(1, top), 20),
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "ConsistencyLevel": "eventual",
    }
    try:
        r = requests.get(
            GRAPH_USERS_URL,
            headers=headers,
            params=params,
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        users = []
        for u in data.get("value", []):
            email = (u.get("mail") or u.get("userPrincipalName") or "").strip()
            if not email:
                continue
            users.append({"email": email, "displayName": (u.get("displayName") or "").strip() or email})
        return users
    except Exception:
        return []

def get_obo_token(user_assertion: str) -> str:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": API_CLIENT_ID,
        "client_secret": API_CLIENT_SECRET,
        "assertion": user_assertion,
        "scope": "https://graph.microsoft.com/.default",
        "requested_token_use": "on_behalf_of"
    }
    resp = requests.post(TOKEN_ENDPOINT, headers=headers, data=data)
    if resp.status_code != 200:
        print(f"[OBO] Token Error: {resp.status_code} {resp.text[:300]}")
        return None
    return resp.json().get("access_token")

def _build_sharepoint_scope_url() -> str:
    normalized = SP_SITE_PATH.strip()
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized

def list_user_sharepoint_files(user_graph_token: str, page_size: int, max_pages: int):
    if not user_graph_token:
        return []
    # _, _, _, sp_site_path, _ = SP_SITE_PATH  
    sp_site_path = SP_SITE_PATH
    paths = [p.strip() for p in sp_site_path.split(",") if p.strip()]
    if not paths:
        paths = ["/Shared Documents"]  # fallback, agar user kuch na de
    path_queries = []
    for p in paths:
        p = p.strip()
        full_path = p.rstrip("/")  # trailing slash remove
        path_queries.append(f'Path:"{full_path}/*"')  # append properly

    kql = f"IsDocument:true ({' OR '.join(path_queries)})"
    print("Generated KQL:", kql)

    headers = {
        "Authorization": f"Bearer {user_graph_token}",
        "Content-Type": "application/json"
    }
    results = []
    offset = 0
    pages = 0
    while pages < max_pages:
        body = {
            "requests": [
                {
                    "entityTypes": ["driveItem"],
                    "query": {"queryString": kql},
                    "from": offset,
                    "size": page_size,
                    "fields": ["name", "webUrl","id"],
                    "trimDuplicates": False
                }
            ]
        }
        resp = requests.post(GRAPH_SEARCH_URL, headers=headers, json=body)
        if resp.status_code != 200:
            print(f"[Search] Error {resp.status_code}: {resp.text[:300]}")
            break
        data = resp.json()
        value = data.get("value", [])
        if not value:
            break
        containers = value[0].get("hitsContainers", [])
        if not containers:
            break
        hc = containers[0]
        hits = hc.get("hits", [])
        for hit in hits:
            resource = hit.get("resource", {})
            name = resource.get("name")
            web_url = resource.get("webUrl")
            item_id = resource.get("id")
            if not name and web_url:
                try:
                    name = urllib.parse.unquote(web_url.rstrip("/").split("/")[-1])
                except Exception:
                    name = "Unknown"
            results.append({
                "name": name or "Unknown",
                "webUrl": web_url,
                "id": item_id
            })
        if not hc.get("moreResultsAvailable"):
            break
        offset += page_size
        pages += 1
    return results

def create_entra_user(username: str, domain: str, display_name: str | None = None, password: str | None = None) -> dict:
    """
    Create a user in Microsoft Entra ID via Graph. Uses the API app (Key Vault API-CLIENT-ID/API-CLIENT-SECRET)
    which must have User.ReadWrite.All with admin consent.
    Returns dict with success (bool), message, and on success userPrincipalName; on error: error, details.
    """
    import re
    import secrets
    try:
        token = get_app_only_token()
        if not token:
            return {
                "success": False,
                "error": "TokenFailed",
                "message": "Failed to acquire Microsoft Graph token (API app from Key Vault).",
            }
    except Exception as e:
        return {
            "success": False,
            "error": "TokenError",
            "message": str(e),
        }
    raw_nick = (username.split("@")[0] if "@" in username else username).strip()
    mail_nickname = re.sub(r"[^a-zA-Z0-9._-]", "", raw_nick).strip("._-")[:64]
    if not mail_nickname:
        mail_nickname = "u" + (re.sub(r"[^a-zA-Z0-9]", "", raw_nick)[:30] or "user")
    mail_nickname = mail_nickname[:64]
    user_principal = username if "@" in username else f"{username}@{domain}"
    if not display_name:
        display_name = (username.split("@")[0] if "@" in username else username).replace(".", " ").title()
    if not password:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        password = "".join(secrets.choice(alphabet) for _ in range(16))
    user_data = {
        "accountEnabled": True,
        "displayName": display_name,
        "mailNickname": mail_nickname,
        "userPrincipalName": user_principal,
        "passwordProfile": {
            "forceChangePasswordNextSignIn": True,
            "password": password,
        },
        "passwordPolicies": "DisablePasswordExpiration",
        "usageLocation": "US",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(GRAPH_USERS_URL, headers=headers, json=user_data)
    except Exception as e:
        return {"success": False, "error": "RequestError", "message": str(e)}
    if response.status_code == 201:
        return {
            "success": True,
            "message": "User created in Microsoft Entra",
            "userPrincipalName": user_principal,
        }
    err = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    err_body = err.get("error", {})
    msg = err_body.get("message", response.text or "Unknown error")
    msg_lower = (msg or "").lower()
    is_duplicate = (
        "userPrincipalName" in msg_lower and "already exists" in msg_lower
    ) or (
        "another object" in msg_lower and "same value" in msg_lower
    ) or (
        "already exists" in msg_lower and ("user" in msg_lower or "principal" in msg_lower)
    )
    if is_duplicate:
        return {
            "success": False,
            "error": "UserAlreadyExists",
            "message": "User already exists in Entra (that userPrincipalName is taken).",
            "userPrincipalName": user_principal,
        }
    if "insufficient privileges" in msg_lower or "forbidden" in msg_lower or "access denied" in msg_lower:
        return {
            "success": False,
            "error": "InsufficientPrivileges",
            "message": (
                "Insufficient privileges. The API app (Key Vault: API-CLIENT-ID / API-CLIENT-SECRET) must have "
                "Microsoft Graph application permission User.ReadWrite.All (resource 00000003-0000-0000-c000-000000000000, "
                "permission 741f803b-c850-494e-b5df-cde7c675a1ca) with admin consent. "
                "In Azure: that app registration → API permissions → Grant admin consent for your tenant."
            ),
        }
    return {
        "success": False,
        "error": f"HTTP_{response.status_code}",
        "message": msg,
        "details": response.text[:500] if response.text else None,
    }
