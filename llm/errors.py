class LLMError(Exception):
    """Base error for LLM module."""


class MissingAPIKeyError(LLMError):
    """Raised when API key is missing."""


class ProviderError(LLMError):
    """Raised when provider call fails."""
