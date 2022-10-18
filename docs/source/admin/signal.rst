Signal
------

A running `signald <https://signald.org/articles/install/>`_ instance is needed and the user
running slidge must have the permission to access its socket.

.. note::
  If you installed slidge and signald with apt/dpkg, add the slidge user to the signald group
  to ensure proper permissions.

Launch slidge with ``--socket /path/to/signald.sock`` (defaults to ``/signald/signald.sock``, which
makes sense in a container context).
