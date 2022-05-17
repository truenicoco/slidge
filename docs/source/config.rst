Configuration
=============

Slidge requires a running and configure XMPP server running and accepting
component connections.

Take a look at ``confs/prosody.cfg.lua`` and ``./docker-compose.yml`` to see
what it looks like.

.. argparse::
   :module: slidge.__main__
   :func: get_parser
   :prog: python -m slidge