"""
Messages
--------

- Direct: yes

Registration
------------

- Needs MMAUTH_TOKEN from web interface (via dev console, inspect cookies)

Roster
------

- JID user parts: mattermost usernames
- Filled on startup, no updates

Presences
--------

- All roster online
- Self: N/A (maybe managed by mattermost server?)

Hints
-----

- Typing: no
- Read markers: no

Extras
------

- File uploads: no
"""

from .gateway import Gateway, Session
