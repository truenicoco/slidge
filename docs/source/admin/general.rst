====================
General instructions
====================


Configure the XMPP server
=========================

To keep this guide generic, we'll talk about running the slidge plugin
``superduper`` that connects to the fictional legacy network "Super Duper Chat Network".

Slidge requires a running and properly configured XMPP server running and accepting
component connections. An upload (XEP:`0363`) component is also required to exchange files
(eg. QR codes) with the gateway and the legacy contacts.

Prosody
-------

Add component
*************

Add a component block below the appropriate virtualhost in ``prosody.cfg.lua``

.. code-block:: lua

    Component "superduper.example.com"
      component_secret = "secret"  -- replace this with a real secret!
      modules_enabled = {"privilege"}

mod_privilege
*************

Use `mod_privilege <https://modules.prosody.im/mod_privilege.html>`_ to allow slidge to:

- manage the gateway user's roster, i.e., automatically add legacy contacts
- impersonate the user to send XMPP carbons for messages and markers sent by the user
  from the official legacy client

Installation
~~~~~~~~~~~~

Starting with prosody 0.12, installing the module is as easy as:

.. code-block:: bash

    prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege

Configuration
~~~~~~~~~~~~~

In ``prosody.cfg.lua``, add ``mod_privilege`` to the ``modules_enabled`` list:

.. code-block:: lua

    VirtualHost "example.com"
      privileged_entities = {
        ["legacy-network.example.com"] = {
          roster = "both";
          message = "outgoing";
        }
      }


Define the gateway component's privileges in the appropriate virtualhost block:

.. code-block:: lua

    VirtualHost "example.com"
      privileged_entities = {
        ["legacy-network.example.com"] = {
          roster = "both";
          message = "outgoing";
        }
      }

Then either restart the prosody server, or reload config. You might need to use
`mod_reload_component <https://modules.prosody.im/mod_reload_components.html>`_
for all changes to be taken into account.

ejabberd
--------

TODO: have someone using ejabberd help me write this

Launch the gateway component
============================

.. note::
    The guide describes how to run slidge as containers with podman, but it is also possible
    to set it up differently, for instance by using the OS's python install or a virtual environment.
    However, no official pypi packages are provided at the moment (it may come at some
    point, maybe even distro packages, who knows…).

Installation
------------

Make sure that podman is installed on your system, e.g. ``apt install podman`` (debian, ubuntu...).
Container images are available on https://hub.docker.com/u/nicocool84

Let's launch the container:

.. code-block:: bash

    podman run --network=host \   # so the xmpp server is available on localhost
      --name=slidge-superduper \  # human-friendly name for the container
      --detach \                  # detach from tty
      docker.io/nicocool84/slidge-superduper:latest \
      --secret=secret \           # secret used to connect, as per the XMPP server config
      --jid=telegram.example.com  # JID of the gateway component, as per the XMPP server config

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
    echo "admins=admin@example.com" > /etc/slidge/conf.d/common.conf
    echo "jid=superduper.example.com" > /etc/slidge/conf.d/superduper.conf
    echo "secret=a_real_secret" >> /etc/slidge/conf.d/superduper.conf


Temporarily login as the system user (as root):

.. code-block:: bash

    su slidge --shell /bin/bash

Create the podman container (as the slidge user):

.. code-block:: bash

    podman run --rm --detach \
       --name superduper \                          # friendly name of the conainter
       --volume /var/lib/slidge:/var/lib/slidge \   # persistent data
       --volume /etc/slidge:/etc/slidge \           # config files
       --log-driver journald \                      # logs in journalctl
       --label "io.containers.autoupdate=image" \   # auto-update via podman dedicated mechanism
       --network=host \                             # make localhost available
       docker.io/nicocool84/slidge-superduper:latest \
       --config=/etc/slidge/superduper.conf         # specific config file for this gateway

Create, launch and enable automatic launch of the container as a systemd service (as the slidge user):

.. code-block:: bash

    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    podman generate systemd --new --name superduper > $HOME/.config/systemd/user/superduper.service
    systemctl --user daemon-reload
    systemctl --user enable --now superduper

Logs can be examined with ``journalctl CONTAINER_NAME=superduper``


Configuration
=============

.. argparse::
   :module: slidge.__main__
   :func: get_parser
   :prog: slidge
