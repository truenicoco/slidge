from typing import NamedTuple

from .base import NODE_PREFIX


class CommandCategory(NamedTuple):
    name: str
    node: str


ADMINISTRATION = CommandCategory("🛷️ Slidge administration", NODE_PREFIX + "admin")
CONTACTS = CommandCategory("👤 Contacts", NODE_PREFIX + "contacts")
GROUPS = CommandCategory("👥 Groups", NODE_PREFIX + "groups")
