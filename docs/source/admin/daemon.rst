===================
Running as a daemon
===================

While you can launch slidge interactively from the command-line, it is recommended
to set up a way to launch slidge automatically on startup, i.e., as a *daemon*.

This page describes how to achieve that with the :ref:`Debian packages (systemd)` or
with :ref:`Containers` (using systemd and podman).

Other options (SysV, docker, ...) are also possible but not documented here (as usual,
contributions are welcome).

.. note::

    In this page we assume that you have fulfilled the basic :ref:`XMPP server config`.

Debian packages (systemd)
=========================

Edit and remove the ``.example`` extension for ``/etc/slidge/conf.d/common.conf``
and ``/etc/slidge/superduper.conf.example``.
Enable and start the service with ``sudo systemctl enable --now slidge@superduper.service``.

Containers
==========

Container install
-----------------

Make sure that podman is installed on your system, e.g. ``apt install podman`` (debian, ubuntu...).
Container images are available on https://hub.docker.com/u/nicocool84

Let's launch the container:

.. code-block:: bash

    podman run --network=host \   # so the xmpp server is available on localhost
      --name=slidge-superduper \  # human-friendly name for the container
      --detach \                  # detach from tty
      docker.io/nicocool84/slidge-superduper:latest \
      --secret=secret \           # secret used to connect, as per the XMPP server config
      --jid=telegram.example.org  # JID of the gateway component, as per the XMPP server config

Congrats, users of your XMPP server can now chat with their buddies on the "Super Duper Chat Network",
yoohoo!

Check the logs via ``podman logs slidge-superduper``

Data persistence
----------------

To keep data persistent between stop/starts (which will inevitably happen during updates),
add volumes to your container.
By default, all persistent data slidge needs is in ``/var/lib/slidge`` inside the container,
so use ``--volume /where/you/want:/var/lib/slidge`` as a ``podman run`` argument.

As a systemd unit
-----------------

.. note::
    The following instructions have been tested with debian bullseye.
    For other distros, they might need to be adapted.

Create a system user named slidge (as root):

.. code-block:: bash

    adduser --system slidge --home /var/lib/slidge

Give permission for this user to use subuids and subgids (as root, required for podman):

.. code-block:: bash

    usermod --add-subuids 200000-201000 --add-subgids 200000-201000 slidge

.. warning::
    Check that the 200000-201000 range does not overlap with any other user's range
    in ``/etc/subuid`` and ``/etc/subgid``

Enable lingering for this user so that its systemd user services start on startup (as root):

.. code-block:: bash

    loginctl enable-linger $(id -u slidge)

Create slidge conf files, to avoid passing everything as CLI arguments (as root):

.. code-block:: bash

    mkdir -p /etc/slidge/conf.d/
    echo "admins=admin@example.org" > /etc/slidge/conf.d/common.conf
    echo "jid=superduper.example.org" > /etc/slidge/conf.d/superduper.conf
    echo "secret=a_real_secret" >> /etc/slidge/conf.d/superduper.conf


Temporarily login as the system user (as root):

.. code-block:: bash

    su slidge --shell /bin/bash

Enable the slidge user to create podman instances (as slidge user):

.. code-block:: bash

    export XDG_RUNTIME_DIR=/run/user/$(id -u)

Create the podman container (as the slidge user):

.. code-block:: bash

    podman run --rm --detach \
       --name superduper \                          # friendly name of the container
       --volume /var/lib/slidge:/var/lib/slidge \   # Map directory for persistent data from host to container
       --volume /etc/slidge:/etc/slidge \           # Map config directory from host to container
       --log-driver journald \                      # logs in journalctl
       --label "io.containers.autoupdate=image" \   # auto-update via podman dedicated mechanism
       --network=host \                             # make localhost available
       docker.io/nicocool84/slidge-superduper:latest \
       --config=/etc/slidge/superduper.conf         # specific config file for this gateway.
                                                    # Every gateway should have a separate config file located in this
                                                    # directory and pointed to using podman.

Create, launch and enable automatic launch of the container as a systemd service (as the slidge user):

.. code-block:: bash

    mkdir -p ~/.config/systemd/user
    podman generate systemd --new --name superduper > $HOME/.config/systemd/user/superduper.service
    systemctl --user daemon-reload
    systemctl --user enable --now superduper

Logs can be examined with ``journalctl CONTAINER_NAME=superduper``
