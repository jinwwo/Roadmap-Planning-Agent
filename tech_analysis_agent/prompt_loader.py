"""
prompt_loader.py
────────────────
Small file-based prompt loader used by Tech Analysis agents.

Prompt variants live under:
  prompts/<agent_name>/<variant>/system.md
  prompts/<agent_name>/<variant>/user.md
"""

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROMPT_ROOT = ROOT / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    agent: str
    variant: str
    system: str
    user_template: str

    @property
    def metadata(self) -> dict:
        return {
            "agent": self.agent,
            "variant": self.variant,
            "system_path": str(
                PROMPT_ROOT / self.agent / self.variant / "system.md"
            ),
            "user_template_path": str(
                PROMPT_ROOT / self.agent / self.variant / "user.md"
            ),
        }

    def render_user(self, **kwargs) -> str:
        return self.user_template.format(**kwargs)


def load_prompt(agent: str, variant: str) -> PromptTemplate:
    prompt_dir = PROMPT_ROOT / agent / variant
    system_path = prompt_dir / "system.md"
    user_path = prompt_dir / "user.md"

    if not system_path.exists() or not user_path.exists():
        available = []
        agent_dir = PROMPT_ROOT / agent
        if agent_dir.exists():
            available = sorted(p.name for p in agent_dir.iterdir() if p.is_dir())
        raise FileNotFoundError(
            f"Prompt variant not found: {agent}/{variant}. "
            f"Available variants: {available}"
        )

    return PromptTemplate(
        agent=agent,
        variant=variant,
        system=system_path.read_text(encoding="utf-8").strip(),
        user_template=user_path.read_text(encoding="utf-8").strip(),
    )
