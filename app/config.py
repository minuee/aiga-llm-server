import os
from dotenv import load_dotenv,dotenv_values
from .common.logger import logger

load_dotenv(override=True)

env_vars = dotenv_values()
## logger.info(f"전체 .env 값 확인: {env_vars}")

class Settings:
    mysql_host: str = os.getenv("MYSQL_HOST")
    mysql_port: int = int(os.getenv("MYSQL_PORT", 3306))
    mysql_user: str = os.getenv("MYSQL_USER")
    mysql_password: str = os.getenv("MYSQL_PASSWORD")
    mysql_db: str = os.getenv("MYSQL_DB")

    project_title = "AIGA"

    azure_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key: str = os.getenv("AZURE_OPENAI_KEY")
    azure_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION")
    azure_api_model: str = os.getenv("AZURE_OPENAI_MODEL")
    azure_summary_api_model: str = os.getenv("AZURE_OPENAI_SUMMARY_MODEL")
    azure_request_timeout: int = int(os.getenv("AZURE_OPENAI_REQUEST_TIMEOUT", 60))

    google_api_key: str = os.getenv("GOOGLE_API_KEY")

    default_locale: str = os.getenv("DEFAULT_LOCALE", "ko")

    validation_enable: bool = os.getenv('VALIDATION_ENABLE') == "true"
    validation_retry_limit: int = int(os.getenv('VALIDATION_RETRY_LIMIT', 3))

    sql_agent_verbose: bool = os.getenv('SQL_AGENT_VERBOSE')  == "true"
    llm_summary_verbose: bool = os.getenv('LLM_SUMMARY_VERBOSE') == "true"
    llm_sql_agent_json_export_verbose: bool = os.getenv('LLM_SQL_AGENT_JSON_EXPORT_VERBOSE') == "true"
    sqlite_directory:str =  os.getenv('SQLITE_DIRECTORY')
    cache_sqlite_directory:str =  os.getenv('CACHE_SQLITE_DIRECTORY')
    llm_sql_agent_cache_verbose: bool = os.getenv('LLM_SQL_AGENT_CACHE_VERBOSE') == "true"
    MESSAGE_MARKDOWN_USE_VERBOSE: bool = os.getenv('MESSAGE_MARKDOWN_USE_VERBOSE') == "true"
    llm_save_location_history_verbose: bool = os.getenv('LLM_SAVE_LOCATION_HISTORY_VERBOSE') == "true"
    LLM_SAVE_ENTRY_HISTORY_VERBOSE: bool = os.getenv('LLM_SAVE_ENTRY_HISTORY_VERBOSE') == "true"
    distance_square_meter: float = float(os.getenv('DISTANCE_SQUARE_METER', 50.0))
    limit_common: int = int(os.getenv('LIMIT_COMMON', 10))

    proactive_restoration_limit: int = int(os.getenv('PROACTIVE_RESTORATION_LIMIT', 10))

    ## - Noh logger.info(f"azure_endpoint: {azure_endpoint}")
    ## - Noh logger.info(f"azure_key: {azure_key}")
    ## - Noh logger.info(f"azure_api_version: {azure_api_version}")
    ## - Noh logger.info(f"azure_api_model: {azure_api_model}")

settings = Settings()