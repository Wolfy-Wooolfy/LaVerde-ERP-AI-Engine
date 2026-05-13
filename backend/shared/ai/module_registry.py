"""AI Module Registry — maps ERP modules to their AI capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class AIModuleSpec:
    """Specification for a single AI-enabled ERP module."""

    name: str
    display_name_en: str
    display_name_ar: str
    intents: list[str]
    intent_handler: Optional[Callable] = field(default=None, repr=False)
    suggested_questions: list[str] = field(default_factory=list)
    chat_endpoint: str = ""


class AIModuleRegistry:
    """Central registry mapping module names to their AI specs."""

    _registry: dict[str, AIModuleSpec] = {}

    @classmethod
    def register(cls, spec: AIModuleSpec) -> None:
        cls._registry[spec.name] = spec

    @classmethod
    def get(cls, name: str) -> Optional[AIModuleSpec]:
        return cls._registry.get(name)

    @classmethod
    def all(cls) -> list[AIModuleSpec]:
        return list(cls._registry.values())

    @classmethod
    def get_all_intents(cls) -> dict[str, str]:
        """Return a flat mapping of intent → module name for routing."""
        result: dict[str, str] = {}
        for spec in cls._registry.values():
            for intent in spec.intents:
                result[intent] = spec.name
        return result
