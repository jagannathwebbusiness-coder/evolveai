class ToolError(Exception):
    """Expected validation or execution failure, safe to show to the user/model."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
