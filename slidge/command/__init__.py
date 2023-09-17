"""
This module implements an unified API to define :term:`adhoc <Ad-hoc Command>`
or :term:`chatbot <Chatbot Command>` commands. Just subclass a :class:`Command`,
and make sures it is imported in your legacy module's ``__init__.py``.
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
