"""cape — collect Aruba UXI (formerly Cape Networks) sensor data."""
from .client import UXIClient
from .secrets import Credentials, fetch_credentials

__all__ = ["UXIClient", "Credentials", "fetch_credentials"]
__version__ = "0.1.0"
