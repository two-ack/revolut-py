# -*- coding: utf-8 -*-
"""
This package defines all library-specific exceptions
"""


class RevolutApiException(Exception):
    """A base class for this project exceptions."""


class TokenExpiredException(RevolutApiException):
    """To be thrown when Revolut API responds with token expired message"""


class ApiChangedException(RevolutApiException):
    """To be thrown when Revolut API has (possibly) changed, rendering our
       current implementation obsolete and requiring attention.
    """

