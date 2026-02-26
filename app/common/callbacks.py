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
        logger.info("on_llm_end triggered.")

        # response.llm_output이 None이 아닌지 먼저 확인
        if response.llm_output is not None:
            token_usage = response.llm_output.get("token_usage", {})
            if token_usage:
                logger.info("Token usage found in on_llm_end.")
                self.total_prompt_tokens += token_usage.get("prompt_tokens", 0)
                self.total_completion_tokens += token_usage.get("completion_tokens", 0)
                self.total_tokens += token_usage.get("total_tokens", 0)
            else:
                logger.warning("No 'token_usage' key found in response.llm_output.")
        else:
            # llm_output이 None인 결정적인 경우를 로깅
            logger.warning("response.llm_output is None in on_llm_end. This may indicate an API error or content filtering.")
