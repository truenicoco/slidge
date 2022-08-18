Signal
------

A running `signald <https://signald.org/articles/install/>`_ instance is needed and the user
running slidge must have the permission to access its socket.

.. argparse::
   :module: slidge.plugins.signal.gateway
   :func: get_parser
   :prog: slidge --legacy-module=slidge.plugins.signal [SLIDGE_OPTS]
