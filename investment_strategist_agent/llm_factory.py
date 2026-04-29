"""
llm_factory.py
──────────────
Provider-agnostic LLM 팩토리.

LLM_PROVIDER 환경변수에 따라 Claude(ChatAnthropic) 또는 Ollama(ChatOllama) 인스턴스를 반환합니다.

- Ollama 모드에서는 `format="json"` 을 자동 설정하여 JSON 파싱 안정성을 높입니다.
- 두 provider 모두 LangChain `BaseChatModel` 인터페이스를 따르므로 호출부는 수정할 필요가 없습니다.
"""

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

    Ollama 의 기본 num_ctx 는 2048 (작음) — 긴 user_prompt + 출력 시 컨텍스트 초과로
    JSON 잘림 → 파싱 실패. num_ctx=8192 로 명시적 확장.
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
            num_ctx=8192,   # ★ 컨텍스트 윈도우 확장 (기본 2048 → 8192)
        )
        if json_mode:
            kwargs["format"] = "json"
        return ChatOllama(**kwargs)

    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=CLAUDE_MODEL,
        anthropic_api_key=ANTHROPIC_API_KEY,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def describe_llm() -> str:
    """현재 LLM provider 상태 요약 (UI / 로그 표시용)"""
    if LLM_PROVIDER == "ollama":
        return f"Ollama @ {OLLAMA_BASE_URL} · model={OLLAMA_MODEL}"
    return f"Anthropic Claude · model={CLAUDE_MODEL}"
