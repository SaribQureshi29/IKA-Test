import os
import sys
import logging
import uuid
import asyncio
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from auth import verify_token
from graph_Service import get_obo_token
from utils import pick_top_files_from_chunks, retrieve_top_chunks, get_allowed_doc_ids, log_to_db_and_file, answer_with_llm, check_if_answer_exists, classify_and_handle_general_chat, MODEL_DEPLOYMENT, build_html_citations
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import pyodbc
from variables import set_chat_payload
from admin import router as admin_router
from config import (get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET,
                    SP_SITE_PATH, SP_EXCLUDE_PATHS, MODEL_DEPLOYMENT)

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
#         print(f"[SQL ERROR] Could not fetch configs in main.py: {e}")
#         return "", "", "", "", ""

# (TENANT_ID,
#  CLIENT_ID,
#  CLIENT_SECRET,
#  SP_SITE_PATH,
#  SP_EXCLUDE_PATHS,
#  MODEL_DEPLOYMENT) = get_values_from_sql()

# credential = ClientSecretCredential(
#     tenant_id=TENANT_ID,
#     client_id=CLIENT_ID,
#     client_secret=CLIENT_SECRET
# )

# kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

# def get_secret(name: str) -> str:
#     return kv_client.get_secret(name).value

ALLOWED_ORIGINS = get_secret("ALLOWED-ORIGINS") #KV

app = FastAPI()

logger = logging.getLogger("system_logger")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Token"],
)

if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(admin_router)

@app.get("/")
async def read_index():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "ok"}

def _static_file(path_rel: str, media_type: str):
    path = os.path.join("static", path_rel)
    if os.path.exists(path):
        return FileResponse(path, media_type=media_type)
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/style.css")
async def read_css():
    return _static_file("style.css", "text/css")

@app.get("/script.js")
async def read_js():
    return _static_file("script.js", "application/javascript")

@app.get("/favicon.ico")
async def favicon():
    """Avoid 404 when browser requests favicon. Serve from static if present."""
    path = os.path.join("static", "favicon.ico")
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/config")
async def get_config():
    """Frontend config (e.g. redirectUri for Azure AD). Set REDIRECT_URI in .env to match Azure app registration."""
    redirect_uri = ALLOWED_ORIGINS
    return {"redirectUri": redirect_uri}


class ChatRequest(BaseModel):
    question: str


@app.post("/chat")
def chat(req: ChatRequest, authorization: str = Header(default=None)):
    try:
        payload, raw_token = verify_token(authorization)
    except Exception as e:
        logger.warning(f"Token invalid: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")
    user_name = payload.get('preferred_username') or payload.get('name') or 'UNKNOWN_USER'
    session_id = str(uuid.uuid4())[:8]
    log_to_db_and_file('INFO', f"Chat start | user={user_name} | session={session_id} | q='{req.question}'")
    graph_token = get_obo_token(raw_token)
    if not graph_token:
        raise HTTPException(status_code=401, detail="Authentication Error: OBO token failed")
    allowed_info = get_allowed_doc_ids(graph_token)
    allowed_doc_ids = allowed_info["doc_ids"]
    files_meta      = allowed_info["files"]
    allowed_names = [f["name"] for f in files_meta]
    allowed_links = [f["webUrl"] for f in files_meta]
    log_to_db_and_file('INFO', f"Allowed doc_ids: {allowed_doc_ids}")
    q = (req.question or "").strip()
    intent_result = classify_and_handle_general_chat(q, MODEL_DEPLOYMENT)
    if intent_result["is_general"]:
        general_response = intent_result["response"]
        log_to_db_and_file('INFO', f"General Chat Response: {intent_result['response']}")
        set_chat_payload(
            user_name=user_name,
            session_id=session_id,
            user_message=req.question,
            agent_response=general_response,
            references=None
        )
        return {
            "answer": general_response,
            "meta": {"retrieved": 0, "sources": [], "References": []}
        }
    if not q or q.lower() in ("list files", "files", "show files"):
        files_response = f"{user_name} has access to {len(files_meta)} documents."
        set_chat_payload(
            user_name=user_name,
            session_id=session_id,
            user_message=req.question,
            agent_response=files_response,
            references=allowed_names
        )
        return {
            "answer": files_response,
            "meta": {"document_count": len(files_meta), "files": allowed_names, "links": allowed_links, "References": allowed_names}
        }
    try:
        initial_hits = retrieve_top_chunks(q, allowed_doc_ids, k=30)
        top_doc_ids = pick_top_files_from_chunks(initial_hits, top_files=1)
        hits = [h for h in initial_hits if h["doc_id"] in top_doc_ids][:8]
        log_to_db_and_file('INFO',    f"Top files selected: {top_doc_ids}")
    except Exception as e:
        logger.exception("Retrieval failed")
        raise HTTPException(status_code=500, detail=f"Retrieval error: {e}")
    if not hits:
        no_content_response = "No relevant content found in your accessible documents."
        set_chat_payload(
            user_name=user_name,
            session_id=session_id,
            user_message=req.question,
            agent_response=no_content_response,
            references=None
        )
        return {"answer": no_content_response, "meta": {"retrieved": 0, "References": []}}
    try:
        if not check_if_answer_exists(initial_hits):
            no_permission_response = "You are not allowed to access this content because it is not in your authorized documents."
            set_chat_payload(
                user_name=user_name,
                session_id=session_id,
                user_message=req.question,
                agent_response=no_permission_response,
                references=None
            )
            return {
                "answer": no_permission_response,
                "meta": {"retrieved": 0, "sources": [], "References": []}
            }
        answer = answer_with_llm(q, hits)
        citations_html = build_html_citations(hits)
        final_answer = f"""
        <div>
          <p>{answer}</p>
          {citations_html}
        </div>
        """
    except Exception as e:
        logger.exception("LLM answer failed")
        raise HTTPException(status_code=500, detail=f"LLM error: {e}")
    sources = [{"title": h["title"], "url": h["url"], "doc_id": h["doc_id"], "score": h["score"]} for h in hits]
    reference_titles = [s["title"] for s in sources] if sources else None
    log_to_db_and_file('INFO', f"Answer returned | sources={sources}")
    set_chat_payload(
        user_name=user_name,
        session_id=session_id,
        user_message=req.question,
        agent_response=answer,
        references=reference_titles
    )
    return {
        "answer": answer,
        "meta": {"retrieved": len(hits), "sources": sources, "References": reference_titles or []}
    }

