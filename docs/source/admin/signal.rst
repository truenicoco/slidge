Signal
------

A running `signald <https://signald.org/articles/install/>`_ instance is needed and the user
running slidge must have the permission to access its socket.

.. note::
  If you slidge and signald with debian packages, use add the slidge user to the signald group
  to ensure proper permissions.

Launch slidge with ``--socket /path/to/signald.sock`` (defaults to ``/signald/signald.sock``, which
makes sense in a container context).
