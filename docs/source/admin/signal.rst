Signal
------

A running `signald <https://signald.org/articles/install/>`_ instance is needed and the user
running slidge must have the permission to access its socket.

Launch slidge with ``--socket /path/to/signald.sock`` (defaults to ``/signald/signald.sock``, which
makes sense in a container context).
