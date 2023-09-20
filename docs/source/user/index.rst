=========
For users
=========

.. include:: note.rst

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
