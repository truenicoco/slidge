Signal
------

A running `signald <https://signald.org/articles/install/>`_ instance is needed and the user
running slidge must have the permission to access its socket.

.. note::
  If you installed slidge and signald with apt/dpkg, add the slidge user to the signald group
  to ensure proper permissions.


.. argparse::
   :filename: source/argparsers.py
   :func: signal
   :prog: slidge --legacy-module slidge.plugins.signal
