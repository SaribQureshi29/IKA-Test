import logging
from sql_db import save_chat_data  
from sql_db import log_to_user

logger = logging.getLogger("chat_payload_logger")
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = logging.FileHandler("user.logs")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False

CHAT_PAYLOAD = {}

def set_chat_payload(user_name: str, session_id: str, user_message: str, agent_response: str,
                     references=None):
    global CHAT_PAYLOAD
    CHAT_PAYLOAD = {
        "USER_NAME": user_name,
        "SESSION_ID": session_id,
        "USER_MESSAGE": user_message,
        "AGENT_RESPONSE": agent_response,
        "REFERENCES": references
    }
    
    # 1. ORIGINAL FILE LOGGING
    logger.info(f"Chat payload set: USER_NAME={user_name}, SESSION_ID={session_id}")
    logger.info(f"User message: {user_message}")
    logger.info(f"Agent response: {agent_response}")
    
    # 2. DATABASE LOGGING
    try:
        log_to_user(
            session_id=session_id,
            user_name=user_name,
            user_message=user_message,
            agent_response=agent_response,
            reference_files=references
        )
    except:
        pass  
    
    try:
        success = save_chat_data(
            session_id=session_id,
            user_name=user_name,
            user_message=user_message,
            agent_response=agent_response,
            references=references
        )
        if success:
            logger.info(f" Chat data saved to Azure SQL: {session_id}")
        else:
            logger.warning(f" Chat data NOT saved to SQL (connection issue): {session_id}")
    except Exception as e:
        logger.error(f" SQL logging error (non-fatal): {str(e)}")