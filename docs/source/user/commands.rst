Commands
========

.. include:: note.rst

Slidge legacy modules may provide additional :term:`commands <Command>`.
You can discover them either by sending "help" to the slidge component's JID
or via :term:`ad-hoc commands <Ad-hoc Command>` (:xep:`0050`),
if your XMPP client supports them.

These commands should be present:


Contacts
--------

List your :term:`legacy contacts <Legacy Contact>`.

Groups
------

List your legacy groups.

Find
----

Search for contacts to chat with.

Sync :term:`Roster`
-------------------

Make sure the :term:`legacy contacts <Legacy Contact>` in your roster matches
your contacts on the :term:`Legacy Network` side.
This is usually not needed, but slidge is beta software and sometimes weird
stuff happen.

Unregister
----------

Unregister to the gateway, ie, stop using slidge, and remove your credentials
from the host running slidge.
