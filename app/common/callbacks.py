from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from typing import Any
from ..common.logger import logger

class TokenCountingCallback(BaseCallbackHandler):
    """LLM 호출의 토큰 사용량을 집계하는 콜백 핸들러"""
    def __init__(self):
        super().__init__()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_tokens = 0
        logger.info("TokenCountingCallback initialized.")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """LLM 호출이 끝날 때마다 자동으로 실행되어 토큰을 누적합니다."""
        logger.info(f"on_llm_end triggered. Response:")
        
        token_usage = response.llm_output.get("token_usage", {})
        if token_usage:
            logger.info(f"Token usage found in on_llm_end:")
            self.total_prompt_tokens += token_usage.get("prompt_tokens", 0)
            self.total_completion_tokens += token_usage.get("completion_tokens", 0)
            self.total_tokens += token_usage.get("total_tokens", 0)
        else:
            logger.warning("No token_usage found in on_llm_end response.llm_output.")
