class OrchestratorError(Exception):
    """Base error for orchestration failures."""


class InputValidationError(OrchestratorError):
    """Raised when agent inputs cannot be safely combined."""


class LLMClientError(OrchestratorError):
    """Raised when the configured LLM cannot return a usable completion."""


class LLMOutputError(OrchestratorError):
    """Raised when the LLM response does not follow the output contract."""

