==========
Plugin API
==========

With ``from slidge import *`` you get access to these classes.
At the very minimum, :class:`.BaseGateway` and :class:`.BaseSession` must be subclassed
for a plugin to work.

The main slidge entrypoint will automatically detect which classes have been
subclassed and use them automagically.
Just subclass await, and launch your plugin with
``slidge --legacy-network=your.importable.plugin``.

.. autoclass:: slidge.BaseGateway
  :members:

.. autoclass:: slidge.BaseSession
  :members:
  :exclude-members: [from_stanza, from_jid, kill_by_jid, send_from_msg, active_from_msg, inactive_from_msg, composing_from_msg, paused_from_msg, displayed_from_msg, correct_from_msg]

You may get away with the generic versions of these twos, but depending on
how users are identified on a legacy network, you might need to subclass
the following classes.

Even if you use their generic implementations, you most likely will
need to call the methods they provide.

.. autoclass:: slidge.LegacyRoster
  :members:

.. autoclass:: slidge.LegacyContact
  :members:

