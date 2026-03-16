import os
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException
from dotenv import load_dotenv
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pyodbc
from config import get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET 

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
