Telegram
--------

.. note::
  Telegram is OK with alternative clients, so as long as you're not doing evil stuff, using slidge
  to interact with the telegram network is fine.
  The plugin uses telegram's official `TDLib <https://tdlib.github.io/td/>`_.

Roster
******

Contact JIDs are of the form ``123456789@slidge-telegram.example.com`` where 123456789 is a telegram ID.
If you want to find the telegram ID of someone using their phone number, use slidge's search feature:
:ref:`Finding legacy contacts`.

Presences
*********

Your contacts' puppet JIDs presence statuses will show when they were last seen online,
and their presence statuses will be set to "away" after 5 minutes.
