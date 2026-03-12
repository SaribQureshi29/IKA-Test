import os
import secrets
import hashlib
import pyodbc
import logging
from datetime import datetime, timedelta, timezone
from config import SQL_SERVER, SQL_DATABASE, SQL_USERNAME, SQL_PASSWORD, SQL_DRIVER
from config import get_secret, TENANT_ID, CLIENT_ID, CLIENT_SECRET

logger = logging.getLogger("sql_logger")
logger.setLevel(logging.INFO)

def get_connection():
    try:
        conn_str = (
            # f"DRIVER={'ODBC Driver 18 for SQL Server'};"
            # f"SERVER={'ika-chat-sql-server.database.windows.net'};"
            # f"DATABASE={'ika-chat-db'};"
            # f"UID={'SqlAdmin'};"
            # f"PWD={'Admin_456!!'};"
            # f"Encrypt={'yes'};"
            # f"Connection Timeout={'120'};"
            # f"TrustServerCertificate=no;"
            f"DRIVER={SQL_DRIVER};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USERNAME};"
            f"PWD={SQL_PASSWORD};"
            f"Encrypt=yes;"
            f"Connection Timeout=120;"
            f"TrustServerCertificate=no;"
        )
        connection = pyodbc.connect(conn_str)
        logger.info("Connected to Azure SQL Database using SQL Authentication")
        return connection
    except pyodbc.Error as e:
        logger.error(f"SQL Connection failed: {str(e)[:200]}")
        return None
    except Exception as e:
        logger.error(f"Unexpected SQL error: {str(e)}")
        return None

def get_available_sql_driver():
    try:
        drivers = pyodbc.drivers()
        logger.info(f"Available ODBC Drivers: {drivers}")
        if "ODBC Driver 18 for SQL Server" in drivers:
            return "ODBC Driver 18 for SQL Server"
        elif "ODBC Driver 17 for SQL Server" in drivers:
            return "ODBC Driver 17 for SQL Server"
        else:
            raise RuntimeError("No suitable ODBC SQL Server driver found")
    except Exception as e:
        logger.error(f"ODBC Driver detection failed: {e}")
        return None

def execute_query(connection, query, params=None):
    if not connection:
        return None
    cursor = connection.cursor()
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        if query.strip().upper().startswith('SELECT'):
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            return columns, rows
        else:
            connection.commit()
            return cursor.rowcount
    except pyodbc.Error as e:
        connection.rollback()
        logger.error(f"SQL Query failed: {e}")
        return None
    finally:
        cursor.close()

def get_config_value(config_key: str) -> str | None:
    """Read a value from Configs table (e.g. ORGANIZATION_DOMAIN). Returns None if not found."""
    connection = get_connection()
    if not connection:
        return None
    try:
        result = execute_query(
            connection,
            "SELECT ConfigValue FROM Configs WHERE ConfigKey = ?",
            (config_key.strip(),),
        )
        if result and result[1] and result[1][0]:
            val = (result[1][0][0] or "").strip()
            return val or None
    except Exception:
        pass
    finally:
        connection.close()
    return None


def set_config_value(config_key: str, config_value: str) -> bool:
    """Write a value to Configs table (upsert). Returns True on success."""
    connection = get_connection()
    if not connection:
        return False
    key = config_key.strip()
    try:
        cursor = connection.cursor()
        cursor.execute("UPDATE Configs SET ConfigValue = ? WHERE ConfigKey = ?", (config_value, key))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO Configs (ConfigKey, ConfigValue) VALUES (?, ?)", (key, config_value))
        connection.commit()
        cursor.close()
        return True
    except pyodbc.Error as e:
        if connection:
            connection.rollback()
        logger.error(f"set_config_value failed: {e}")
        return False
    finally:
        connection.close()

def ensure_tables():
    connection = get_connection()
    if not connection:
        return False
    try:
        cursor = connection.cursor()
        tables_to_create = [
            ("Configs", """
            CREATE TABLE Configs (
                ConfigKey NVARCHAR(255) NOT NULL PRIMARY KEY,
                ConfigValue NVARCHAR(MAX) NULL,
                UpdatedAt DATETIME DEFAULT GETDATE()
            )
        """),
            ("System_Logs", """
                CREATE TABLE System_Logs (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    LogTimestamp DATETIME DEFAULT GETDATE(),
                    LogLevel NVARCHAR(10) NOT NULL,
                    LogMessage NVARCHAR(MAX) NOT NULL,
                    SessionID NVARCHAR(255) NULL,
                    UserName NVARCHAR(255) NULL
                )
            """),
            ("User_Logs", """
                CREATE TABLE User_Logs (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    LogTimestamp DATETIME DEFAULT GETDATE(),
                    SessionID NVARCHAR(255) NOT NULL,
                    UserName NVARCHAR(255) NOT NULL,
                    UserMessage NVARCHAR(MAX) NULL,
                    AgentResponse NVARCHAR(MAX) NULL,
                    ReferenceFiles NVARCHAR(MAX) NULL
                )
            """),
            ("Chat_UserSessions", """
                CREATE TABLE Chat_UserSessions (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    SessionID NVARCHAR(255) NOT NULL,
                    UserName NVARCHAR(255) NOT NULL,
                    CreatedAt DATETIME DEFAULT GETDATE()
                )
            """),
            ("Chat_UserMessages", """
                CREATE TABLE Chat_UserMessages (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    SessionID NVARCHAR(255) NOT NULL,
                    UserMessage TEXT NOT NULL,
                    CreatedAt DATETIME DEFAULT GETDATE()
                )
            """),
            ("Chat_AgentResponses", """
                CREATE TABLE Chat_AgentResponses (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    SessionID NVARCHAR(255) NOT NULL,
                    AgentResponse TEXT NOT NULL,
                    CreatedAt DATETIME DEFAULT GETDATE()
                )
            """),
            ("Chat_References", """
                CREATE TABLE Chat_References (
                    ID INT IDENTITY(1,1) PRIMARY KEY,
                    SessionID NVARCHAR(255) NOT NULL,
                    ReferenceFiles TEXT,
                    CreatedAt DATETIME DEFAULT GETDATE()
                )
            """),
            ("Admin_Users", """
                        CREATE TABLE Admin_Users (
                            ID INT IDENTITY(1,1) PRIMARY KEY,
                            Username NVARCHAR(255) NOT NULL UNIQUE,
                            PasswordHash NVARCHAR(255) NOT NULL,
                            CreatedAt DATETIME DEFAULT GETDATE()
                        )
                    """),
            ("Admin_Sessions", """
                        CREATE TABLE Admin_Sessions (
                            ID INT IDENTITY(1,1) PRIMARY KEY,
                            Token NVARCHAR(255) NOT NULL UNIQUE,
                            Username NVARCHAR(255) NOT NULL,
                            ExpiresAt DATETIME NOT NULL,
                            CreatedAt DATETIME DEFAULT GETDATE()
                        )
                    """),
            ("Admin_Logs", """
                        CREATE TABLE Admin_Logs (
                            ID INT IDENTITY(1,1) PRIMARY KEY,
                            LogTimestamp DATETIME DEFAULT GETDATE(),
                            Username NVARCHAR(255) NOT NULL,
                            Action NVARCHAR(64) NOT NULL,
                            Details NVARCHAR(MAX) NULL
                        )
                    """),
        ]
        # Drop obsolete tables if they exist (do not create them)
        for drop_name in ("Chat_Domains", "Chat_UserRoles"):
            try:
                cursor.execute(f"SELECT 1 FROM sys.tables WHERE name = '{drop_name}'")
                if cursor.fetchone():
                    cursor.execute(f"DROP TABLE {drop_name}")
                    connection.commit()
                    logger.info(f"Dropped obsolete table: {drop_name}")
            except pyodbc.Error as e:
                logger.warning(f"Drop {drop_name} failed: {e}")
                connection.rollback()
        for table_name, create_sql in tables_to_create:
            try:
                cursor.execute(f"SELECT 1 FROM sys.tables WHERE name = '{table_name}'")
                if not cursor.fetchone():
                    cursor.execute(create_sql)
                    connection.commit()
                    logger.info(f"Created table: {table_name}")
                else:
                    logger.info(f"Table already exists: {table_name}")
            except pyodbc.Error as e:
                logger.error(f"Error with table {table_name}: {str(e)[:200]}")
                connection.rollback()
                continue
        # Fix Admin_Users / Admin_Sessions if they exist with wrong schema (e.g. wrong column names)
        try:
            cursor.execute("""
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'Admin_Users' AND COLUMN_NAME = 'PasswordHash'
            """)
            if cursor.fetchone():
                pass  # schema ok
            else:
                cursor.execute("SELECT 1 FROM sys.tables WHERE name = 'Admin_Users'")
                if cursor.fetchone():
                    logger.info("Recreating Admin_Sessions and Admin_Users (schema fix)")
                    cursor.execute("IF OBJECT_ID('Admin_Sessions', 'U') IS NOT NULL DROP TABLE Admin_Sessions")
                    cursor.execute("IF OBJECT_ID('Admin_Users', 'U') IS NOT NULL DROP TABLE Admin_Users")
                    connection.commit()
                    cursor.execute("""
                        CREATE TABLE Admin_Users (
                            ID INT IDENTITY(1,1) PRIMARY KEY,
                            Username NVARCHAR(255) NOT NULL UNIQUE,
                            PasswordHash NVARCHAR(255) NOT NULL,
                            CreatedAt DATETIME DEFAULT GETDATE()
                        )
                    """)
                    cursor.execute("""
                        CREATE TABLE Admin_Sessions (
                            ID INT IDENTITY(1,1) PRIMARY KEY,
                            Token NVARCHAR(255) NOT NULL UNIQUE,
                            Username NVARCHAR(255) NOT NULL,
                            ExpiresAt DATETIME NOT NULL,
                            CreatedAt DATETIME DEFAULT GETDATE()
                        )
                    """)
                    connection.commit()
                    logger.info("Admin_Users and Admin_Sessions recreated")
        except pyodbc.Error as e:
            logger.error(f"Admin tables schema fix failed: {e}")
            connection.rollback()
        # Migrate SharePoint_Whitelist / SharePoint_Blacklist into Configs, then drop those tables
        try:
            cursor.execute("SELECT 1 FROM sys.tables WHERE name = 'SharePoint_Whitelist'")
            if cursor.fetchone():
                cursor.execute("SELECT SiteUrl FROM SharePoint_Whitelist ORDER BY ID")
                whitelist_rows = cursor.fetchall()
                existing = ""
                cursor.execute("SELECT ConfigValue FROM Configs WHERE ConfigKey = 'SP_SITE_PATH'")
                row = cursor.fetchone()
                if row and row[0]:
                    existing = (row[0] or "").strip()
                current_list = [x.strip() for x in existing.split(",") if x.strip()]
                for r in whitelist_rows:
                    url = (r[0] or "").strip()
                    if url and url not in current_list:
                        current_list.append(url)
                new_value = ",".join(current_list)
                cursor.execute("UPDATE Configs SET ConfigValue = ? WHERE ConfigKey = 'SP_SITE_PATH'", (new_value,))
                if cursor.rowcount == 0:
                    cursor.execute("INSERT INTO Configs (ConfigKey, ConfigValue) VALUES ('SP_SITE_PATH', ?)", (new_value,))
                cursor.execute("DROP TABLE SharePoint_Whitelist")
                connection.commit()
                logger.info("Migrated SharePoint_Whitelist to Configs.SP_SITE_PATH and dropped table")
            cursor.execute("SELECT 1 FROM sys.tables WHERE name = 'SharePoint_Blacklist'")
            if cursor.fetchone():
                cursor.execute("SELECT SiteUrl FROM SharePoint_Blacklist ORDER BY ID")
                blacklist_rows = cursor.fetchall()
                existing = ""
                cursor.execute("SELECT ConfigValue FROM Configs WHERE ConfigKey = 'SP_EXCLUDE_PATHS'")
                row = cursor.fetchone()
                if row and row[0]:
                    existing = (row[0] or "").strip()
                current_list = [x.strip() for x in existing.split(",") if x.strip()]
                for r in blacklist_rows:
                    url = (r[0] or "").strip()
                    if url and url not in current_list:
                        current_list.append(url)
                new_value = ",".join(current_list)
                cursor.execute("UPDATE Configs SET ConfigValue = ? WHERE ConfigKey = 'SP_EXCLUDE_PATHS'", (new_value,))
                if cursor.rowcount == 0:
                    cursor.execute("INSERT INTO Configs (ConfigKey, ConfigValue) VALUES ('SP_EXCLUDE_PATHS', ?)", (new_value,))
                cursor.execute("DROP TABLE SharePoint_Blacklist")
                connection.commit()
                logger.info("Migrated SharePoint_Blacklist to Configs.SP_EXCLUDE_PATHS and dropped table")
        except pyodbc.Error as e:
            logger.error(f"SharePoint list migration/drop failed: {e}")
            connection.rollback()
        cursor.close()
        _seed_admin_if_needed()
        logger.info("All chat and admin tables verified")
        return True
    except Exception as e:
        logger.error(f"Table creation failed: {str(e)}")
        return False
    finally:
        connection.close()

def save_chat_data(session_id, user_name, user_message, agent_response, references=None):
    connection = get_connection()
    if not connection:
        return False
    try:
        execute_query(connection,
                      "INSERT INTO Chat_UserSessions (SessionID, UserName) VALUES (?, ?)",
                      (session_id, user_name))
        execute_query(connection,
                      "INSERT INTO Chat_UserMessages (SessionID, UserMessage) VALUES (?, ?)",
                      (session_id, user_message))
        execute_query(connection,
                      "INSERT INTO Chat_AgentResponses (SessionID, AgentResponse) VALUES (?, ?)",
                      (session_id, agent_response))
        if references:
            if isinstance(references, list):
                references = ', '.join(references)
            execute_query(connection,
                          "INSERT INTO Chat_References (SessionID, ReferenceFiles) VALUES (?, ?)",
                          (session_id, references))
        logger.info(f"Chat data saved to SQL for session: {session_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to save chat data: {str(e)}")
        return False
    finally:
        connection.close()

def log_to_system(log_level: str, log_message: str, session_id: str = None,
                  user_name: str = None) -> bool:
    connection = get_connection()
    if not connection:
        return False
    try:
        cursor = connection.cursor()
        query = """
            INSERT INTO System_Logs 
            (LogLevel, LogMessage, SessionID, UserName)
            VALUES (?, ?, ?, ?)
        """
        cursor.execute(query, (log_level, log_message, session_id, user_name))
        connection.commit()
        cursor.close()
        return True
    except Exception:
        return False
    finally:
        connection.close()

def log_to_user(session_id: str, user_name: str, user_message: str = None,
                agent_response: str = None, reference_files: str = None) -> bool:
    connection = get_connection()
    if not connection:
        return False
    try:
        cursor = connection.cursor()
        if isinstance(reference_files, list):
            reference_files = ', '.join(reference_files)
        query = """
            INSERT INTO User_Logs 
            (SessionID, UserName, UserMessage, AgentResponse, ReferenceFiles)
            VALUES (?, ?, ?, ?, ?)
        """
        cursor.execute(query, (session_id, user_name, user_message,
                               agent_response, reference_files))
        connection.commit()
        cursor.close()
        return True
    except Exception:
        return False
    finally:
        connection.close()

def _hash_password(password: str) -> str:
    """Hash password with salt. Use same ADMIN_PASSWORD_SALT everywhere or hashes won't match."""
    p = (password or "").strip()
    salt = os.getenv("ADMIN_PASSWORD_SALT", "ika-admin-salt")
    return hashlib.sha256(f"{salt}{p}".encode("utf-8")).hexdigest()


# Single admin account only (no other admin accounts).
# _ADMIN_USERNAME() = get_secret("ADMINEMAIL")
# _ADMIN_PASSWORD = get_secret("ADMINPASSWORD")

def _ADMIN_USERNAME():
    return get_secret("ADMINEMAIL")

def _ADMIN_PASSWORD():
    return get_secret("ADMINPASSWORD")


def _seed_admin_if_needed():
    """Ensure ika admin user exists in Admin_Users (insert if missing, update password hash if present)."""
    connection = get_connection()
    if not connection:
        return
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM Admin_Users WHERE LOWER(Username) = LOWER(?)", (_ADMIN_USERNAME(),))
        exists = cursor.fetchone() is not None
        h = _hash_password(_ADMIN_PASSWORD())
        if not exists:
            cursor.execute(
                "INSERT INTO Admin_Users (Username, PasswordHash) VALUES (?, ?)",
                (_ADMIN_USERNAME(), h)
            )
            connection.commit()
            logger.info(f"Seeded admin user: {_ADMIN_USERNAME()}")
        else:
            cursor.execute("UPDATE Admin_Users SET PasswordHash = ? WHERE LOWER(Username) = LOWER(?)", (h, _ADMIN_USERNAME()))
            connection.commit()
        cursor.close()
    except Exception as e:
        logger.error(f"Seed admin failed: {e}")
        if connection:
            connection.rollback()
    finally:
        if connection:
            connection.close()


def log_admin_action(username: str, action: str, details: str | None = None) -> bool:
    """Log an admin action to Admin_Logs. Actions: LOGIN, ADD_WHITELIST, REMOVE_WHITELIST, ADD_BLACKLIST, REMOVE_BLACKLIST, ADD_ADMIN, PROVISION_USER."""
    connection = get_connection()
    if not connection:
        return False
    try:
        execute_query(
            connection,
            "INSERT INTO Admin_Logs (Username, Action, Details) VALUES (?, ?, ?)",
            ((username or "").strip(), (action or "").strip(), (details or "")[:4000] if details else None)
        )
        return True
    except Exception:
        return False
    finally:
        connection.close()


def validate_admin(username: str, password: str) -> bool:
    """Validate admin credentials against database. Username is case-insensitive; password is trimmed."""
    connection = get_connection()
    if not connection:
        return False
    try:
        uname = (username or "").strip()
        if not uname:
            return False
        _, rows = execute_query(
            connection,
            "SELECT PasswordHash FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
            (uname,)
        )
        if not rows:
            return False
        expected_hash = rows[0][0]
        return _hash_password(password) == expected_hash
    finally:
        connection.close()


def create_admin_session(username: str) -> str:
    """Create an admin session token, return token. Expires in 24 hours."""
    token = secrets.token_urlsafe(32)
    connection = get_connection()
    if not connection:
        return ""
    try:
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        execute_query(
            connection,
            "INSERT INTO Admin_Sessions (Token, Username, ExpiresAt) VALUES (?, ?, ?)",
            (token, username.strip(), expires)
        )
        return token
    finally:
        connection.close()


# Placeholder hash for Azure-only admins (no password; they sign in via Azure AD only).
_AZURE_ONLY_PASSWORD_PLACEHOLDER = "__AZURE_ONLY_NO_PASSWORD__"


def add_admin_user(username: str, password: str | None = None) -> tuple[str | None, str | None]:
    """
    Add or update an admin user in Admin_Users. Password is hashed (trimmed).
    If password is None, uses a placeholder hash so the user can only access via Azure (check-access).
    Returns (status, error_msg): status is "created" or "updated", or None on failure; error_msg is set on failure.
    """
    username = (username or "").strip()
    if not username:
        return (None, "Username required")
    pwd_value = (password or "").strip() if password is not None else _AZURE_ONLY_PASSWORD_PLACEHOLDER
    if not pwd_value:
        return (None, "Password required or use email-only flow (validated in Azure)")
    pwd_hash = _hash_password(pwd_value)
    connection = get_connection()
    if not connection:
        return (None, "Database connection failed")
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT Username FROM Admin_Users WHERE LOWER(Username) = LOWER(?)", (username,))
        existing = cursor.fetchone()
        if existing is not None:
            stored_username = existing[0]
            cursor.execute("UPDATE Admin_Users SET PasswordHash = ? WHERE Username = ?", (pwd_hash, stored_username))
            connection.commit()
            cursor.close()
            return ("updated", None)
        cursor.execute(
            "INSERT INTO Admin_Users (Username, PasswordHash) VALUES (?, ?)",
            (username, pwd_hash),
        )
        connection.commit()
        cursor.close()
        return ("created", None)
    except pyodbc.Error as e:
        if connection:
            connection.rollback()
        logger.error(f"add_admin_user failed: {e}")
        return (None, "Username may already exist or database error")
    finally:
        connection.close()


def get_admin_username_for_token(token: str) -> str | None:
    """Return username if token is valid and not expired, else None."""
    if not token:
        return None
    connection = get_connection()
    if not connection:
        return None
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT Username FROM Admin_Sessions WHERE Token = ? AND ExpiresAt > GETUTCDATE()",
            (token,)
        )
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else None
    finally:
        connection.close()


def get_admin_username_if_exists(identifier: str) -> str | None:
    """
    Return stored Admin_Users.Username if identifier (e.g. Azure preferred_username)
    matches one, else None.
    """

    identifier = (identifier or "").strip().lower()

    if not identifier:
        return None

    connection = get_connection()
    if not connection:
        return None

    try:
        _, rows = execute_query(
            connection,
            """
            SELECT Username
            FROM Admin_Users
            WHERE LOWER(LTRIM(RTRIM(Username))) = ?
            """,
            (identifier,),
        )

        if rows and rows[0]:
            return (rows[0][0] or "").strip().lower()

        return None

    finally:
        connection.close()


def list_all_admin_usernames() -> list:
    """Return list of all admin usernames from Admin_Users (for super admin to list/remove)."""
    connection = get_connection()
    if not connection:
        return []
    try:
        _, rows = execute_query(
            connection,
            "SELECT Username FROM Admin_Users ORDER BY Username",
            None,
        )
        if not rows:
            return []
        return [r[0] for r in rows]
    finally:
        connection.close()


def remove_admin_user(username: str) -> tuple[bool, str | None]:
    """
    Remove an admin from Admin_Users and invalidate their sessions. Only non–super-admin can be removed.
    Returns (True, None) on success, (False, error_message) on failure.
    """
    username = (username or "").strip()
    if not username:
        return (False, "Username required")
    if username.lower() == _ADMIN_USERNAME().lower():
        return (False, "Cannot remove super admin")
    connection = get_connection()
    if not connection:
        return (False, "Database connection failed")
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT Username FROM Admin_Users WHERE LOWER(Username) = LOWER(?)", (username,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return (False, "Admin not found")
        stored_username = row[0]
        cursor.execute("DELETE FROM Admin_Sessions WHERE Username = ?", (stored_username,))
        cursor.execute("DELETE FROM Admin_Users WHERE Username = ?", (stored_username,))
        connection.commit()
        cursor.close()
        return (True, None)
    except pyodbc.Error as e:
        if connection:
            connection.rollback()
        logger.error(f"remove_admin_user failed: {e}")
        return (False, "Database error")
    finally:
        connection.close()


def get_sharepoint_whitelist() -> list:
    """Return list of whitelisted paths/URLs from Configs.SP_SITE_PATH (comma-separated). Same source as ingest."""
    raw = get_config_value("SP_SITE_PATH") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def get_sharepoint_blacklist() -> list:
    """Return list of blacklisted paths/URLs from Configs.SP_EXCLUDE_PATHS (comma-separated). Same source as ingest."""
    raw = get_config_value("SP_EXCLUDE_PATHS") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def add_sharepoint_whitelist(site_url: str) -> bool:
    """Add a path/URL to SP_SITE_PATH in Configs (comma-separated)."""
    site_url = (site_url or "").strip()
    if not site_url:
        return False
    current = get_sharepoint_whitelist()
    if site_url in current:
        return True
    current.append(site_url)
    return set_config_value("SP_SITE_PATH", ",".join(current))


def remove_sharepoint_whitelist(site_url: str) -> bool:
    """Remove a path/URL from SP_SITE_PATH in Configs."""
    site_url = (site_url or "").strip()
    if not site_url:
        return False
    current = get_sharepoint_whitelist()
    if site_url not in current:
        return True
    current = [x for x in current if x != site_url]
    return set_config_value("SP_SITE_PATH", ",".join(current))


def add_sharepoint_blacklist(site_url: str) -> bool:
    """Add a path/URL to SP_EXCLUDE_PATHS in Configs (comma-separated)."""
    site_url = (site_url or "").strip()
    if not site_url:
        return False
    current = get_sharepoint_blacklist()
    if site_url in current:
        return True
    current.append(site_url)
    return set_config_value("SP_EXCLUDE_PATHS", ",".join(current))


def remove_sharepoint_blacklist(site_url: str) -> bool:
    """Remove a path/URL from SP_EXCLUDE_PATHS in Configs."""
    site_url = (site_url or "").strip()
    if not site_url:
        return False
    current = get_sharepoint_blacklist()
    if site_url not in current:
        return True
    current = [x for x in current if x != site_url]
    return set_config_value("SP_EXCLUDE_PATHS", ",".join(current))

# Run table creation on import
ensure_tables()
