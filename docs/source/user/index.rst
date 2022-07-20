=========
For users
=========

Slidge can be used to send and receive messages with any XMPP client, such as
`Conversations <https://conversations.im>`_,
`Dino <https://dino.im>`_,
`Gajim <https://gajim.org>`_
or `BeagleIM <https://beagle.im/>`_.

To make it work, you must "link" the foreign accounts you want to use with XMPP account
by registering to the slidge server component with a client
that supports in band registration, such as `Gajim <https://gajim.org/>`_ or `Psi <https://psi-im.org/>`_.
This has to be done once per account you want to use

For instance, in gajim, go to the "accounts" menu, select "discover services"
and you get this window where you can configure your other accounts.

.. figure:: gajim.png
   :scale: 50 %
   :alt: Gajim service discovery

   The service discovery interface of gajim.

.. toctree::
   :maxdepth: 2

   common
   telegram
   signal
