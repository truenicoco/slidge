==========
For admins
==========

.. include:: note.rst

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
