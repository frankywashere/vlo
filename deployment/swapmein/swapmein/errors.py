class PublicError(Exception):
    def __init__(self, message: str, status_code: int = 400, *,
                 code: str | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after
