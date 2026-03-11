import os
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pyodbc
from config import get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET

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
#         print(f"[SQL ERROR] Could not fetch configs in auth.py: {e}")
#         return "", "", "", "", ""

# (TENANT_ID,
#  CLIENT_ID,
#  CLIENT_SECRET,
#  SP_SITE_PATH,
#  SP_EXCLUDE_PATHS) = get_values_from_sql()

# credential = ClientSecretCredential(
#     tenant_id=TENANT_ID,
#     client_id=CLIENT_ID,
#     client_secret=CLIENT_SECRET
# )

# kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

# def get_secret(name: str) -> str:
#     return kv_client.get_secret(name).value

AUDIENCE     = get_secret("AUDIENCE") #KV
REQUIRED_SCOPE = get_secret("REQUIRED-SCOPE") #KV

ALLOWED_ISSUERS = {
    f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    f"https://sts.windows.net/{TENANT_ID}/",
}

JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
_jwk_client = PyJWKClient(JWKS_URL)

def verify_token(auth_header: str):
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            options={"verify_iss": False},
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    iss = payload.get("iss")
    if iss not in ALLOWED_ISSUERS:
        raise HTTPException(status_code=401, detail=f"Invalid issuer: {iss}")
    if payload.get("tid") != TENANT_ID:
        raise HTTPException(status_code=401, detail="Token tenant mismatch")
    scp = payload.get("scp", "")
    scopes = scp.split()
    if REQUIRED_SCOPE not in scopes:
        raise HTTPException(status_code=403, detail="Missing required scope")
    return payload, token
