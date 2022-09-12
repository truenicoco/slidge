=======
General
=======

Make sure that ``python3-gdbm`` is available on your system.
You can check that this is the case by running ``python3 -c "import dbm.gnu"``
which will exit with return code 0 if it's available.
This requirement might be removed in the future.

Every slidge plugin runs in an independent process and requires its own
entries in the XMPP server config.
To keep this guide generic, we'll talk about running the slidge plugin
``superduper`` that connects to the fictional legacy network "Super Duper Chat Network".
