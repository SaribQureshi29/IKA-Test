from heapq import nlargest
from graph_Service import list_user_sharepoint_files
import base64
import requests
from sql_db import log_to_system
import logging
import os
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
#         print(f"[SQL ERROR] Could not fetch configs in utils.py: {e}")
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

API_CLIENT_ID = get_secret("API-CLIENT-ID") #KV
API_CLIENT_SECRET = get_secret("API-CLIENT-SECRET") #KV

AZURE_OPENAI_ENDPOINT    = get_secret("AZURE-OPENAI-ENDPOINT").rstrip("/") #KV
AZURE_OPENAI_API_VERSION = get_secret("AZURE-OPENAI-API-VERSION") #KV
EMBEDDING_DEPLOYMENT     = get_secret("EMBEDDING-DEPLOYMENT") #KV
AZURE_OPENAI_API_KEY     = get_secret("AZURE-OPENAI-API-KEY-V2") #KV
AZURE_SEARCH_ENDPOINT = get_secret("AZURE-SEARCH-ENDPOINT").rstrip("/") #KV
AZURE_SEARCH_KEY     = get_secret("AZURE-SEARCH-KEY") #KV
AZURE_SEARCH_INDEX   = get_secret("AZURE-SEARCH-INDEX") #KV
ALLOWED_ORIGINS = get_secret("ALLOWED-ORIGINS") #KV

logger = logging.getLogger("system_logger")
logger.setLevel(logging.INFO)
if not logger.handlers:
    file_handler = logging.FileHandler("system.logs", encoding="utf-8")
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s",datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False

def log_to_db_and_file(level: str, message: str, **kwargs):
    try:
        log_to_system(log_level=level, log_message=message, **kwargs)
    except Exception:
        pass
    try:
        getattr(logger, level.lower(), logger.info)(message)
    except Exception:
        logger.info(message)

def make_safe_key(raw: str) -> str:
    token = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    if token.startswith("_"):
        token = "k" + token
    return token

def share_id_from_weburl(web_url: str) -> str:
    b64 = base64.urlsafe_b64encode(web_url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b64}"

def resolve_drive_item_id(graph_token: str, web_url: str) -> Optional[str]:
    try:
        sid = share_id_from_weburl(web_url)
        url = f"https://graph.microsoft.com/v1.0/shares/{sid}/driveItem?$select=id"
        r = requests.get(url, headers={"Authorization": f"Bearer {graph_token}"}, timeout=20)
        if r.status_code == 200:
            return (r.json() or {}).get("id")
        else:
            logger.info(f"[ResolveID] {r.status_code} for {web_url} :: {r.text[:200]}")
            return None
    except Exception as e:
        logger.info(f"[ResolveID] Exception for {web_url}: {e}")
        return None


def get_allowed_doc_ids(graph_token: str) -> Dict[str, Any]:
    items = list_user_sharepoint_files(graph_token, page_size=200, max_pages=50)
    logger.info(f"Allowed items per user are: {items}")
    print(f"Allowed items per user are: {items}")
    by_url: Dict[str, Dict[str, Any]] = {}
    for it in items:
        webUrl = it.get("webUrl") or ""
        name = it.get("name") or ""
        item_id = it.get("id")
        if not webUrl:
            continue
        if webUrl in by_url:
            continue
        by_url[webUrl] = {"name": name, "webUrl": webUrl, "id": item_id}
    files_meta: List[Dict[str, Any]] = []
    doc_id_set: set[str] = set()
    for webUrl, meta in by_url.items():
        name = meta["name"]
        item_id = meta.get("id")
        safe_ids: set[str] = set()
        safe_from_web = make_safe_key(webUrl)
        safe_ids.add(safe_from_web)
        doc_id_set.add(safe_from_web)
        if item_id:
            safe_from_item = make_safe_key(item_id)
            safe_ids.add(safe_from_item)
            doc_id_set.add(safe_from_item)
            logger.info(f"[AllowedDocs] Direct ID: {name} -> {safe_from_item}")
        else:
            did = resolve_drive_item_id(graph_token, webUrl)
            if did:
                safe_from_item = make_safe_key(did)
                safe_ids.add(safe_from_item)
                doc_id_set.add(safe_from_item)
        files_meta.append({
            "name": name,
            "webUrl": webUrl,
            "safe_ids": list(safe_ids)
        })
    unique_names = [f["name"] for f in files_meta]
    unique_links = [f["webUrl"] for f in files_meta]
    log_to_db_and_file('INFO', f"User allowed documents count: {len(files_meta)}")
    log_to_db_and_file('INFO', f"User allowed document names: {unique_names}")
    log_to_db_and_file('INFO', f"User allowed document links: {unique_links}")
    log_to_db_and_file('INFO', f"Allowed doc_ids: {list(doc_id_set)}")
    print('INFO', f"Allowed doc_ids: {list(doc_id_set)}")
    return {"doc_ids": list(doc_id_set), "files": files_meta}

def embed_query(question: str) -> List[float]:
    url = f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{EMBEDDING_DEPLOYMENT}/embeddings?api-version={AZURE_OPENAI_API_VERSION}"
    headers = {"Content-Type": "application/json"}
    if AZURE_OPENAI_API_KEY:
        headers["api-key"] = AZURE_OPENAI_API_KEY
    elif token_provider:
        try:
            headers["Authorization"] = f"Bearer {token_provider()}"
        except Exception as e:
            raise RuntimeError(f"Failed to obtain AAD token for embeddings: {e}")
    else:
        raise RuntimeError("No AOAI key or AAD token provider available for embeddings.")
    resp = requests.post(url, headers=headers, json={"input": [question]}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"[Embeddings] {resp.status_code} {resp.text[:300]}")
    data = resp.json()
    return data["data"][0]["embedding"]

search_client = SearchClient(
    endpoint=AZURE_SEARCH_ENDPOINT,
    index_name=AZURE_SEARCH_INDEX,
    credential=AzureKeyCredential(AZURE_SEARCH_KEY)
)

def retrieve_top_chunks(question: str, allowed_doc_ids: List[str], k: int) -> List[Dict[str, Any]]:
    if not allowed_doc_ids:
        return []
    q_emb = embed_query(question)
    vq = VectorizedQuery(
        vector=q_emb,
        k_nearest_neighbors=k,
        fields="embedding"
    )
    BATCH_IDS = 250
    merged: List[Dict[str, Any]] = []
    def _run_batch(ids_slice: List[str]) -> List[Dict[str, Any]]:
        values = ",".join(ids_slice)
        odata_filter = f"search.in(doc_id, '{values}', ',')"
        results = search_client.search(
            search_text=question,
            vector_queries=[vq],
            filter=odata_filter,
            query_type="semantic",
            semantic_configuration_name="default",
            query_answer="extractive",
            query_caption="extractive",
            top=k
        )
        out = []
        for r in results:
            score = r.get("@search.reranker_score")
            out.append({
                "title":   r.get("title"),
                "content": r.get("content"),
                "url":     r.get("url"),
                "doc_id":  r.get("doc_id"),
                "score":   score,
            })
        return out
    for i in range(0, len(allowed_doc_ids), BATCH_IDS):
        slice_ids = allowed_doc_ids[i:i+BATCH_IDS]
        batch_hits = _run_batch(slice_ids)
        merged.extend(batch_hits)
    def _score_key(h): return (h.get("score") if h.get("score") is not None else float("-inf"))
    top_hits = nlargest(k, merged, key=_score_key)
    srcs = [f"[{idx+1}] {h.get('title')} | {h.get('url')} | score={h.get('score')}" for idx, h in enumerate(top_hits)]
    log_to_db_and_file('INFO', f"Retriever hits ({len(top_hits)}): {srcs}")
    return top_hits

def pick_top_files_from_chunks(chunks: List[Dict[str, Any]], top_files: int):
    best_score_per_doc = {}
    for c in chunks:
        doc_id = c.get("doc_id")
        score  = c.get("score")
        if doc_id not in best_score_per_doc:
            best_score_per_doc[doc_id] = score
        else:
            best_score_per_doc[doc_id] = max(best_score_per_doc[doc_id], score)
    top_docs = nlargest(
        top_files,
        best_score_per_doc.items(),
        key=lambda x: x[1]
    )
    return [doc_id for doc_id, _ in top_docs]

client = None
token_provider = None

try:
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )
    token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        azure_ad_token_provider=token_provider,
        api_version="2024-02-15-preview"
    )
except Exception as e:
    logger.warning(f"Azure OpenAI init failed (optional for chat): {e}")

def build_html_citations(hits):
    seen = set()
    lines = ["<h3>Citations:</h3>", "<ul>"]
    for h in hits:
        url = h.get("url")
        title = h.get("title") or "Document"
        if not url or url in seen:
            continue
        seen.add(url)
        lines.append(
            f'<li>'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'{title}'
            f'</a>'
            f'</li>'
        )
    lines.append("</ul>")
    return "\n".join(lines)



def answer_with_llm(question: str, hits: List[Dict[str, Any]]) -> str:
    if not client:
        return "Azure OpenAI client is not initialized."
    MAX_CHARS = 2000
    ctx_lines = []
    for i, h in enumerate(hits, start=1):
        snippet = (h.get("content") or "")[:MAX_CHARS]
        title   = h.get("title") or ""
        url     = h.get("url") or ""
        ctx_lines.append(f"[{i}] Title: {title}\nURL: {url}\nExcerpt:\n{snippet}\n")
    context = "\n\n".join(ctx_lines) if ctx_lines else "No context."
    system = (
        "You are an enterprise assistant. Answer strictly from the provided context. "
        "Do NOT mention sources, references, document names, sections, or citations "
        "inside the answer text. "
        "Provide a clean, well-structured answer only."
        "Mention each line in bullets with proper formating"
    )
    user = (
        f"Question:\n{question}\n\nContext:\n{context}\n\n"
        "Return a concise answer first, then bullet citations."
    )
    resp = client.chat.completions.create(
        model=MODEL_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ],
        temperature=0.2,
        max_tokens=600
    )
    answer = resp.choices[0].message.content.strip()
    try:
        usage = getattr(resp, "usage", None)
        if usage:
            log_to_db_and_file('INFO', f"LLM usage: prompt={usage.prompt_tokens} completion={usage.completion_tokens} total={usage.total_tokens}")
    except Exception:
        pass
    return answer

def check_if_answer_exists(top_chunks: List[Dict[str, Any]]) -> bool:
    if not top_chunks:
        return False
    MIN_SCORE_THRESHOLD = 1.5
    best_score = top_chunks[0].get("score") or 0
    print(top_chunks[0])
    print("Best score is: ",best_score)
    if best_score < MIN_SCORE_THRESHOLD:
        return False
    return True


def classify_and_handle_general_chat(query: str, model_name: str) -> dict:
    system_prompt = (
        "You are an intelligent assistant router. Your job is to classify user queries.\n"
        "1. IF the user query is a greeting, small talk, compliment, or general question "
        "(e.g., 'Hi', 'Who are you?', 'Thanks', 'Good morning'), answer it politely and briefly.\n"
        "2. IF the user query requires retrieving information from company documents, policies, "
        "or specific data (e.g., 'What is leave policy?', 'Show me files', 'Help me with HR'), "
        "reply with EXACTLY one word: 'SEARCH_DOCS'.\n"
        "Do not answer document-related questions yourself."
    )
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query}
        ]
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=100
        )
        content = response.choices[0].message.content.strip()
        if "SEARCH_DOCS" in content:
            return {"is_general": False, "response": None}
        return {"is_general": True, "response": content}
    except Exception as e:
        return {"is_general": False, "response": None}