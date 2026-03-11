# config.py
import os
import pyodbc
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

# Step 1: Read bootstrap values from App Service Environment Variables
TENANT_ID     = os.getenv("TENANT_ID")
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
KEY_VAULT_URL = os.getenv("KEY_VAULT_URL", "https://ikakv.vault.azure.net/")
SQL_DRIVER    = "{ODBC Driver 18 for SQL Server}"

# Step 2: Create KV client
credential = ClientSecretCredential(
    tenant_id=TENANT_ID,
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET
)
kv_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)

def get_secret(name: str) -> str:
    return kv_client.get_secret(name).value

# Step 3: SQL credentials from KV
SQL_SERVER   = get_secret("SQL-SERVER")
SQL_DATABASE = get_secret("SQL-DATABASE")
SQL_USERNAME = get_secret("SQL-USERNAME")
SQL_PASSWORD = get_secret("SQL-PASSWORD")

# Step 4: Remaining config from SQL
def get_values_from_sql():
    conn_str = f'DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};PORT=1433;DATABASE={SQL_DATABASE};UID={SQL_USERNAME};PWD={SQL_PASSWORD}'
    try:
        with pyodbc.connect(conn_str) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT ConfigKey, ConfigValue 
                    FROM Configs 
                    WHERE ConfigKey IN ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 
                                        'SP_SITE_PATH', 'SP_EXCLUDE_PATHS', 'MODEL_DEPLOYMENT')
                """)
                rows = cursor.fetchall()
                config_dict = {row.ConfigKey: row.ConfigValue for row in rows}
                return (
                    config_dict.get('TENANT_ID', ""),
                    config_dict.get('CLIENT_ID', ""),
                    config_dict.get('CLIENT_SECRET', ""),
                    config_dict.get('SP_SITE_PATH', ""),
                    config_dict.get('SP_EXCLUDE_PATHS', ""),
                    config_dict.get('MODEL_DEPLOYMENT', "")
                )
    except Exception as e:
        print(f"[SQL ERROR] {e}")
        return "", "", "", "", "", ""

(TENANT_ID,
 CLIENT_ID,
 CLIENT_SECRET,
 SP_SITE_PATH,
 SP_EXCLUDE_PATHS,
 MODEL_DEPLOYMENT) = get_values_from_sql()