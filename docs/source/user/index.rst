=========
For users
=========

.. note::

  These are the generic user docs for slidge. You might be interested in these
  docs too, for legacy network-specific opotions:

  - `slidgnal <https://slidge.im/slidgnal/user.html>`_
  - `slidge-whatsapp <https://slidge.im/slidge-whatsapp/user.html>`_
  - `slidgram <https://slidge.im/slidgram/user.html>`_
  - `slidcord <https://slidge.im/slidcord/user.html>`_
  - `matteridge <https://slidge.im/matteridge/user.html>`_
  - `sleamdge <https://slidge.im/sleamdge/user.html>`_
  - `skidge <https://slidge.im/skidge/user.html>`_
  - `messlidger <https://slidge.im/messlidger/user.html>`_
  - `matridge <https://slidge.im/matridge/user.html>`_

Slidge is an XMPP server component that can be used to send and receive messages with
`any XMPP client <https://xmpp.org/software/clients>`_,
such as
`Movim <https://movim.eu>`_,
`Conversations <https://conversations.im>`_,
`Dino <https://dino.im>`_,
`Gajim <https://gajim.org>`_
or `BeagleIM <https://beagle.im/>`_,
to name a few.
Your contacts on the legacy network are given a "puppet JID"
of the type ``username@slidge.example.org``,
that you can use to interact with them, just as you would with
any normal XMPP user.
The contact's ``username`` depends on the slidge plugin you use, for instance
on signal, it is the phone number of the user you want to reach.

.. warning::
  Slidge acts as alternative client, logged on as you, running on your XMPP server.
  For some networks, that is not a problem at all (signal, telegram, mattermost), but
  this means breaking the terms of use and/or trigger automated security measures (account
  lock, etc.) for some other networks. See :ref:`Keeping a low profile`.

.. toctree::
   general
   register
   contacts
   commands
   low_profile
