Slidge Design
=============

The main slidge entrypoint will automatically detect which classes have been
subclassed and use them automagically.
Just subclass away, and launch your legacy module with
``slidge --legacy-module=your.importable.legacy_module``.

At the very minimum, you will need to subclass :class:`~slidge.BaseGateway` and
:class:`~slidge.BaseSession` for a legacy module to be functional.

JID local parts to legacy IDs
-----------------------------

You probably also want to subclass :class:`~slidge.contact.LegacyRoster` and
:class:`~slidge.group.LegacyBookmarks` to define how
:term:`JID local parts<JID Local Part>`
map to legacy user or contact IDs.
Defining which local parts map to proper valid user legacy IDs is crucial
to discriminate between JIDs that map to a
:class:`~slidge.contact.LegacyContact` and
those that map to a :class:`~slidge.group.LegacyMUC`.
You should override
:meth:`~slidge.contact.LegacyRoster.jid_username_to_legacy_id`,
:meth:`~slidge.contact.LegacyRoster.legacy_id_to_jid_username`,
:meth:`~slidge.group.LegacyBookmarks.jid_username_to_legacy_id`,
and
:meth:`~slidge.group.LegacyBookmarks.legacy_id_to_jid_username`
in your custom :class:`~slidge.contact.LegacyRoster` and
:class:`~slidge.group.LegacyBookmarks`
classes, and raise an appropriate :class:`~slixmpp.exceptions.XMPPError`
when called with an invalid argument.

Fetching info from the legacy service
-------------------------------------

By subclassing :class:`~slidge.contact.LegacyContact` and
:class:`~slidge.group.LegacyMUC`,
you will be able
to define how :term:`XMPP Entities<XMPP Entity>` update information about
themselves, such as their user-facing name and the :term:`Avatar` that
represents them.
This is done by overriding
:meth:`slidge.contact.LegacyContact.update_info`
and
:meth:`slidge.group.LegacyMUC.update_info`,
in which you should raise an :class:`~slixmpp.exceptions.XMPPError`
in case their :attr:`~slidge.contact.LegacyContact.legacy_id`
attribute is not valid.

Pre-filling contacts and groups
-------------------------------

The coroutines
:meth:`slidge.contact.LegacyRoster.fill()` and
:meth:`slidge.group.LegacyBookmarks.fill()`
will be awaited just after :meth:`~slidge.BaseSession.login()`
and should be used to pre-fill known "friends" and groups.
