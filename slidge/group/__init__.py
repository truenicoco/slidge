"""
Everything related to groups.
"""

from ..util.types import MucType
from .bookmarks import LegacyBookmarks
from .participant import LegacyParticipant
from .room import LegacyMUC

__all__ = ("LegacyBookmarks", "LegacyParticipant", "LegacyMUC", "MucType")
