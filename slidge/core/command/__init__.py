"""
This module implements an unified way to define ad-hoc or chatbot-type commands
"""

from . import admin, register, user  # noqa: F401
from .base import (
    Command,
    CommandAccess,
    CommandResponseType,
    Confirmation,
    Form,
    FormField,
    SearchResult,
    TableResult,
)

__all__ = (
    "Command",
    "CommandAccess",
    "CommandResponseType",
    "Confirmation",
    "Form",
    "FormField",
    "SearchResult",
    "TableResult",
)
