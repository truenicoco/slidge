============
Installation
============

Dockerhub
---------

Containers for arm64 and amd64 are available on `docker hub <https://hub.docker.com/u/nicocool84>`_.
The slidge-whatsapp arm64 container is kindly provided by `raver <https://hub.docker.com/u/ravermeister>`_.
See :ref:`Containers` for more details.

debian
------

A debian package containing slidge and a bunch of legacy modules is available at
`<https://git.sr.ht/~nicoco/slidge-debian>`_.

Debian packages for *bookworm* (amd64 and arm64)
are built on each push to master as artifacts of
this `build job <https://builds.sr.ht/~nicoco/slidge/commits/master/debian.yml?>`_.

A repo is maintained by IGImonster. Refer to the README of
`<https://git.sr.ht/~nicoco/slidge-debian>`_ for setup instructions.

See :ref:`Debian packages` for information about how to launch slidge as a daemon via systemd.

pipx
----

.. image:: https://badge.fury.io/py/slidge.svg
  :alt: PyPI package
  :target: https://pypi.org/project/slidge/

Tagged releases are uploaded to `pypi <https://pypi.org/project/slidge/>`_
and should be installable on any distro with `pipx`.

Make sure that ``python3-gdbm`` is available on your system.
You can check that this is the case by running ``python3 -c "import dbm.gnu"``
which will exit with return code 0 if it's available.

.. code-block:: bash

    pipx install slidge
    slidge --legacy-module=your_importable_legacy_module

If you're looking for the bleeding edge, download an artifact
`here <https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml?>`_.
