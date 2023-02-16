Configuration
=============

.. note::
  For the debian package, edit the ``/etc/slidge/conf.d/common.conf`` and
  ``/etc/slidge/*.conf`` files

By default, slidge uses all config files found in ``/etc/slidge/conf.d/*``.
You can change this using the ``SLIDGE_CONF_DIR`` env var, eg
``SLIDGE_CONF_DIR=/path/dir1:/path/dir2:/path/dir3``.

Common config
-------------

.. argparse::
   :module: slidge.__main__
   :func: get_parser
   :prog: slidge

Plugin specific
---------------

There are also plugin-specific options for some legacy services.

.. toctree::
   signal
   telegram
   discord
   facebook
   whatsapp
