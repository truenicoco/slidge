"""
Messages
--------

- Direct: yes

Registration
------------

- Use API keys from https://my.telegram.org/apps
- Registering a new phone number: untested but should work

Roster
------

- JID user parts: telegram user IDs
- Filled on startup, no updates
- Search user by phone number available via Jabber Search.
  Could be nice to implement auto search when sending to +XXXXXXXX@slidge

Presences
--------

- All roster online
- Self: N/A

Hints
-----

- Typing: yes
- Read markers: yes

Extras
------

- File uploads: yes
"""

from .client import TelegramClient
from .config import get_parser
from .contact import Contact, Roster
from .gateway import Gateway
from .session import Session
