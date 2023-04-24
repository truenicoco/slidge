============
Installation
============

Dockerhub
---------

Containers for arm64 and amd64 are available on `docker hub <https://hub.docker.com/u/nicocool84>`_.
See :ref:`Containers` for more details.

debian
------

A debian package containing slidge a bunch of legacy modules is available at
`<https://git.sr.ht/~nicoco/slidge-debian>`_.

.. image:: https://slidge.im/debian-release.svg
  :alt: debian version badge for the release channel

.. image:: https://slidge.im/debian-nightly.svg
  :alt: debian version badge for the nightly channel

Debian packages for *bullseye* (amd64 and arm64)
are built on each push to master as artifacts of
this `build job <https://builds.sr.ht/~nicoco/slidge/commits/master/debian.yml?>`_.

A repo is maintained by IGImonster. To use it do this (as root):

.. code-block:: bash

    # trust the repo's key
    wget -O- http://deb.slidge.im/repo/slidge.gpg.key \
      |gpg --dearmor \
      |tee /usr/share/keyrings/slidge.gpg > /dev/null
    # add the repo, replace 'release' with 'nightly' if you're feeling adventurous
    echo "deb [signed-by=/usr/share/keyrings/slidge.gpg] http://deb.slidge.im/repo/debian release main" \
      > /etc/apt/sources.list.d/slidge.list
    # install
    apt update && apt install slidge -y
    # launch
    slidge --legacy-module=slidge.plugins.whatsapp

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
