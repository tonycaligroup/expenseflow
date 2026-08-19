class ExpenseFlowError(Exception):
    """Structured error surfaced by deterministic ExpenseFlow code."""

    def __init__(self, code, message, retryable=False, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self):
        data = {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            data["details"] = self.details
        return data
