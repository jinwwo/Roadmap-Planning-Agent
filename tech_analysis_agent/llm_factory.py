"""
llm_factory.py
──────────────
Provider-agnostic LLM 팩토리.

LLM_PROVIDER 환경변수에 따라 Claude(ChatAnthropic) 또는 Ollama(ChatOllama) 인스턴스를 반환합니다.

주요 특징:
- Ollama 모드에서는 `format="json"` 을 자동 설정하여 JSON 파싱 안정성을 높입니다.
- 두 provider 모두 LangChain `BaseChatModel` 인터페이스를 따르므로 호출부는 수정할 필요가 없습니다.
"""

from typing import Optional
from config import (
    LLM_PROVIDER,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
)


def get_llm(max_tokens: int = 4096, json_mode: bool = True, temperature: float = 0.0):
    """
    Provider-agnostic LLM 인스턴스 반환.

    Parameters
    ----------
    max_tokens : int
        최대 생성 토큰 수.
    json_mode : bool
        True 면 Ollama의 format="json" 을 활성화합니다 (파싱 안정성).
        Claude의 경우 프롬프트로 제어하므로 이 플래그는 무시됩니다.
    temperature : float
        샘플링 온도. 분석 작업은 0 권장.

    Returns
    -------
    BaseChatModel
    """
    if LLM_PROVIDER == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as e:
            raise ImportError(
                "langchain-ollama 패키지가 필요합니다: pip install langchain-ollama"
            ) from e

        kwargs = dict(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=temperature,
            num_predict=max_tokens,
        )
        if json_mode:
            kwargs["format"] = "json"
        return ChatOllama(**kwargs)

    # default: anthropic
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=CLAUDE_MODEL,
        anthropic_api_key=ANTHROPIC_API_KEY,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def describe_llm() -> str:
    """현재 LLM provider 상태 요약 (UI 표시용)"""
    if LLM_PROVIDER == "ollama":
        return f"Ollama @ {OLLAMA_BASE_URL} · model={OLLAMA_MODEL}"
    return f"Anthropic Claude · model={CLAUDE_MODEL}"
