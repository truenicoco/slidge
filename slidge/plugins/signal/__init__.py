"""
Messages
--------

- Direct: yes

Registration
------------

- Registering a new phone number is possible (must receive SMSes).
- Linking to an existing signal account is possible by flashing a QR code
  sent via chat messages with the slidge component.

Roster
------

- JID user parts: phone numbers, starting with +
- Filled on startup, no updates.
- Sending message to any number seems to just work.

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

- File uploads: no
"""

from .contact import Contact, Roster
from .gateway import Gateway
from .session import Session
