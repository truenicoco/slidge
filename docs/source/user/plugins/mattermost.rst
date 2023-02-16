Mattermost
----------

.. note::
  There is not central mattermost instance,
  so check with your instance admin if third party clients are OK with them.
  They should be, as the `mattermost API <https://api.mattermost.com/>`_
  is meant to be used for all sorts of funny things.

Roster
******

Your contacts' puppet JIDs are of the form ``john.doe@slidge-mattermost.example.org`` where
``john.doe`` is their mattermost usernames.
Your roster is filled on startup with the users you interacted with, eg, have a
"direct message channel" with.

Notes
*****

There are no 'contact has read' markers in mattermost, so don't expect them on the XMPP side.