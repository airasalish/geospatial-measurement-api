"""Domain errors. The API layer maps these to HTTP responses in ``app.main``."""


class GeoAPIError(Exception):
    """Base class for errors that are safe to show to API clients."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedFileType(GeoAPIError):
    status_code = 415
    code = "unsupported_file_type"


class FileTooLarge(GeoAPIError):
    status_code = 413
    code = "file_too_large"


class InvalidGeoFile(GeoAPIError):
    """The upload has a supported extension but its content can't be read."""

    status_code = 422
    code = "invalid_file"


class NotFound(GeoAPIError):
    status_code = 404
    code = "not_found"
