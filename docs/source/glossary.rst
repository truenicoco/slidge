Glossary
========

.. glossary::
    User
        An XMPP user using slidge.

    Legacy [#f1]_ network
        The messaging network slidge communicates with.

    Contact
        A user of the legacy network that can communicate with the xmpp user.

    Official client
        The reference client(s) for a legacy network. Examples: telegram-android and telegram-desktop for
        the telegram network

    Carbons
        In the XMPP world, carbons are messages sent from one client to another to rapidly synchronize
        chat history views. In slidge, this refers to actions of the user on an official client. They are synchronized
        between official clients and XMPP using carbons.

.. rubric:: Footnotes

.. [#f1] "Legacy" may sound weird since XMPP is pretty old now, but this attempts to follow
    the convention of :xep:`0100`