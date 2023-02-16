==================
XMPP server config
==================

You must choose a JID without a local part, (eg ``superduper.example.com``) for the gateway,
and a "secret" (ie, a password) for slidge to authenticate to the XMPP server.
Slidge usually connects to the XMPP server via ``localhost`` (see :ref:`Configuration`).

Slidge uses different containers/processes for each gateway. Therefore administrators
should setup these steps for each individual gateway. This is because each gateway
makes use of an individual JID (such as ``telegram.example.com``, ``whatsapp.example.com``, etc).

This documentation explains how to do that for
`prosody <https://prosody.im/doc/components>`_
and `ejabberd <https://docs.ejabberd.im/developer/hosts/>`_.
If you know how to set up slidge with other XMPP servers, please contribute to the docs. ;-)

Prosody
-------

Add a component block below the appropriate virtualhost in ``prosody.cfg.lua``

.. code-block:: lua

    Component "superduper.example.com"
      component_secret = "secret"  -- replace this with a real secret!
      modules_enabled = {"privilege"}  -- see the "Privilege" section for this to work

ejabberd
--------

.. code-block:: yaml

    listen:
      -
        ip: 127.0.0.1
        port: 5347
        module: ejabberd_service
        hosts:
          superduper.example.com:
            password: secret

The 'hosts' domain can be any given subdomain as long as the domain is pointing to the server's ip running ejabberd.
Example: Telegram.example.com, whatsapp.example.com etc.

The subdomain's FQDN (example.com) should be listed under the top level 'hosts'.
Example:

.. code-block:: yaml

        hosts:
          - "example.com"

