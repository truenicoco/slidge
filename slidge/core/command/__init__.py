"""
This module implements an unified way to define ad-hoc or chatbot-type commands
"""

from . import admin, register, user
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
