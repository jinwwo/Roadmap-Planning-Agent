"""
llm_factory.py
──────────────
Provider-agnostic LLM 팩토리.

LLM_PROVIDER 환경변수에 따라 Claude(ChatAnthropic) 또는 Ollama(ChatOllama) 인스턴스를 반환합니다.
sibling 에이전트들의 llm_factory 와 동일한 계약(interface)을 따릅니다.
"""

import os
from config import (
    LLM_PROVIDER,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    OLLAMA_MODEL,
    OLLAMA_BASE_URL,
)


def get_llm(max_tokens: int = 4096, json_mode: bool = True, temperature: float = 0.0):
    """Provider-agnostic LLM 인스턴스 반환."""
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
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384") or 16384),   # ★ 컨텍스트 윈도우 (기본 16K · OLLAMA_NUM_CTX 로 override)
            keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "24h"),   # 모델 메모리 유지 (default 24h)
            client_kwargs={"timeout": int(os.getenv("OLLAMA_TIMEOUT_SEC", "600") or 600)},
        )
        if json_mode:
            kwargs["format"] = "json"
        return _maybe_wrap_no_think(ChatOllama(**kwargs))

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


# ── /no_think 옵션 (Qwen3 시리즈의 thinking 모드 비활성화) ─────
# 환경변수 OLLAMA_NO_THINK=1 이면 SystemMessage 끝에 "/no_think" 자동 삽입.
# 응답 속도 ↑, reasoning ↓. 모델이 thinking 안 쓰면 무해.

class _NoThinkOllama:
    """ChatOllama 를 감싸 invoke 시 system message 끝에 /no_think 추가."""

    def __init__(self, base):
        self._base = base

    def invoke(self, messages, **kwargs):
        try:
            from langchain_core.messages import SystemMessage
        except Exception:
            return self._base.invoke(messages, **kwargs)
        if not isinstance(messages, list):
            return self._base.invoke(messages, **kwargs)
        new_msgs = []
        injected = False
        for m in messages:
            if not injected and getattr(m, "type", "") == "system":
                new_msgs.append(SystemMessage(content=(m.content or "") + "\n\n/no_think"))
                injected = True
            else:
                new_msgs.append(m)
        return self._base.invoke(new_msgs, **kwargs)

    def __getattr__(self, name):
        return getattr(self._base, name)


def _maybe_wrap_no_think(llm):
    flag = (os.getenv("OLLAMA_NO_THINK") or "").strip().lower()
    if flag in ("1", "true", "yes"):
        return _NoThinkOllama(llm)
    return llm

