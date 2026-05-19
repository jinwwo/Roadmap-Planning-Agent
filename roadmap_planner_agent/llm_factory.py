"""
llm_factory.py
──────────────
Provider-agnostic LLM 팩토리.

LLM_PROVIDER 환경변수에 따라 Claude(ChatAnthropic) 또는 Ollama(ChatOllama) 인스턴스를 반환합니다.

- Ollama 모드에서는 `format="json"` 을 자동 설정하여 JSON 파싱 안정성을 높입니다.
- 두 provider 모두 LangChain `BaseChatModel` 인터페이스를 따르므로 호출부는 수정할 필요가 없습니다.
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
    """
    Provider-agnostic LLM 인스턴스 반환.

    Parameters
    ----------
    max_tokens : int
        최대 생성 토큰 수.
    json_mode : bool
        True 면 Ollama 의 format="json" 을 활성화합니다 (파싱 안정성).
        Claude 의 경우 프롬프트로 제어하므로 이 플래그는 무시됩니다.
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

        # OLLAMA_NUM_PREDICT 는 floor — 호출자 max_tokens 가 더 크면 그대로 사용
        num_predict = max_tokens
        np_env = os.getenv("OLLAMA_NUM_PREDICT")
        if np_env:
            try:
                num_predict = max(num_predict, int(np_env))
            except ValueError:
                pass

        kwargs = dict(
            model=OLLAMA_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=temperature,
            num_predict=num_predict,
            num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384") or 16384),
            keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "24h"),
            client_kwargs={"timeout": int(os.getenv("OLLAMA_TIMEOUT_SEC", "600") or 600)},
        )
        if (os.getenv("OLLAMA_NO_THINK") or "").strip().lower() in ("1", "true", "yes"):
            kwargs["reasoning"] = False
        fj_env = (os.getenv("OLLAMA_FORMAT_JSON") or "").strip().lower()
        use_format_json = json_mode and fj_env not in ("0", "false", "no")
        if use_format_json:
            kwargs["format"] = "json"
        return _maybe_wrap_no_think(ChatOllama(**kwargs))

    # default: anthropic
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
# 27b+ 에서 thinking + format=json 버그 회피용. system message 끝에 /no_think 자동 삽입.

class _NoThinkOllama:
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
