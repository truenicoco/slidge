==================
XMPP server config
==================

You must choose a JID without a local part, (eg ``superduper.example.org``) for the gateway,
and a "secret" (ie, a password) for slidge to authenticate to the XMPP server.
Slidge usually connects to the XMPP server via ``localhost`` (see :ref:`Configuration`).

Slidge uses different containers/processes for each gateway. Therefore administrators
should setup these steps for each individual gateway. This is because each gateway
makes use of an individual JID (such as ``telegram.example.org``, ``whatsapp.example.com``, etc).

This documentation explains how to do that for
`prosody <https://prosody.im/doc/components>`_
and `ejabberd <https://docs.ejabberd.im/developer/hosts/>`_.
If you know how to set up slidge with other XMPP servers, please contribute to the docs. ;-)

Prosody
-------

Add a component block below the appropriate virtualhost in ``prosody.cfg.lua``

.. code-block:: lua

    Component "superduper.example.org"
      component_secret = "secret"  -- replace this with a real secret!
      modules_enabled = {"privilege"} -- optional, additional config required to make it work

For the last line, see :ref:`Privileges` for more info about how what it does and how to set it up entirely

ejabberd
--------

.. code-block:: yaml

    listen:
      -
        ip: 127.0.0.1
        port: 5347
        module: ejabberd_service
        hosts:
          superduper.example.org:
            password: secret

The 'hosts' domain can be any given subdomain as long as the domain is pointing to the server's ip running ejabberd.
Examples: telegram.example.org, whatsapp.example.com, etc.
