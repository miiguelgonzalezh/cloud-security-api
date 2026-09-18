"""Fetchers: una funcion por dashboard, todas devuelven un FetchResult."""

from .base import FetchError, FetchResult, RetryPolicy, safe_fetch

__all__ = ["FetchError", "FetchResult", "RetryPolicy", "safe_fetch"]
