"""msl — MyStudyLife from the terminal."""

__version__ = "0.1.0"

from .client import (  # noqa: F401
    AuthExpired,
    Credentials,
    LoginError,
    MSLClient,
    MSLError,
    clear_session,
    load_session,
    resolve_credentials,
    save_session,
)
