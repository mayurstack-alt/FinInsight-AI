"""Safe application errors shared by API and feature services."""


class ApplicationError(Exception):
    def __init__(self, status_code: int, code: str, public_message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.public_message = public_message
