from backend.core.exceptions import CRMAIEngineError


class AIServiceError(CRMAIEngineError):
    """Base exception for all AI service errors."""


class AIProviderError(AIServiceError):
    """Raised when the AI provider (OpenAI) returns an error."""


class AIRateLimitError(AIServiceError):
    """Raised when the AI provider rate limit is exceeded."""


class AITimeoutError(AIServiceError):
    """Raised when an AI request times out."""


class AIInvalidResponseError(AIServiceError):
    """Raised when the AI returns an unparseable or invalid response."""


class BudgetExceededError(AIServiceError):
    """Raised when the monthly AI budget hard stop is triggered."""

    def __init__(self, spent: float, budget: float) -> None:
        self.spent = spent
        self.budget = budget
        super().__init__(f"Monthly AI budget exhausted: ${spent:.4f} / ${budget:.2f}")


class AIFeatureDisabledError(AIServiceError):
    """Raised when an AI feature flag is turned off."""
