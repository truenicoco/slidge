==========
For admins
==========

.. note::

  For legacy module-specific options, refer to their own docs:

  - `slidgnal <https://slidge.im/slidgnal/config.html>`_
  - `slidge-whatsapp <https://slidge.im/slidge-whatsapp/config.html>`_
  - `slidgram <https://slidge.im/slidgram/config.html>`_
  - `slidcord <https://slidge.im/slidcord/config.html>`_
  - `matteridge <https://slidge.im/matteridge/config.html>`_
  - `sleamdge <https://slidge.im/sleamdge/config.html>`_
  - `skidge <https://slidge.im/skidge/config.html>`_
  - `messlidger <https://slidge.im/messlidger/config.html>`_
  - `matridge <https://slidge.im/matridge/config.html>`_

Slidge uses :xep:`0114` (Jabber Component Protocol) to communicate with
an XMPP server.
Every slidge plugin runs in an independent process and requires its own
entries in the XMPP server config.
To keep this guide generic, we'll talk about running the slidge plugin
``superduper`` that connects to the fictional legacy network "Super Duper Chat Network".


.. toctree::

  install
  config/index
  component
  attachments
  privilege
  daemon
  examples/index
