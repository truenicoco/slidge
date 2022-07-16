==========
Plugin API
==========

With ``from slidge import *`` you get access to these classes.
At the very minimum, ``BaseGateway`` and ``BaseSession`` must be subclassed
for a plugin to work.

.. autoclass:: slidge.BaseGateway
  :members:

.. autoclass:: slidge.BaseSession
  :members:

You may get away with the generic versions of these twos, but depending on
how users are identified on a legacy network, you might need to subclass
the following classes.

.. autoclass:: slidge.LegacyRoster
  :members:

.. autoclass:: slidge.LegacyContact
  :members:

