import os
from dotenv import load_dotenv,dotenv_values
from .common.logger import logger

load_dotenv(override=True)

env_vars = dotenv_values()
logger.info(f"전체 .env 값 확인: {env_vars}")

class Settings:
    mysql_host: str = os.getenv("MYSQL_HOST")
    mysql_port: int = int(os.getenv("MYSQL_PORT", 3306))
    mysql_user: str = os.getenv("MYSQL_USER")
    mysql_password: str = os.getenv("MYSQL_PASSWORD")
    mysql_db: str = os.getenv("MYSQL_DB")

    azure_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key: str = os.getenv("AZURE_OPENAI_KEY")
    azure_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION")
    azure_api_model: str = os.getenv("AZURE_OPENAI_MODEL")
    azure_summary_api_model: str = os.getenv("AZURE_OPENAI_SUMMARY_MODEL")


    validation_enable: bool = os.getenv('VALIDATION_ENABLE') == "true"
    validation_retry_limit: int = int(os.getenv('VALIDATION_RETRY_LIMIT', 3))

    sql_agent_verbose: bool = os.getenv('SQL_AGENT_VERBOSE')  == "true"
    sqlite_directory:str =  os.getenv('SQLITE_DIRECTORY')
    ## - Noh logger.info(f"azure_endpoint: {azure_endpoint}")
    ## - Noh logger.info(f"azure_key: {azure_key}")
    ## - Noh logger.info(f"azure_api_version: {azure_api_version}")
    ## - Noh logger.info(f"azure_api_model: {azure_api_model}")

settings = Settings()