Glossary
========

.. glossary::
    User
        An XMPP user using slidge.

    Legacy [#f1]_ network
        The messaging network slidge communicates with.

    Legacy Contact
        A user of the legacy network that can communicate with the xmpp user.

    Official client
        The reference client(s) for a legacy network. Examples: telegram-android
        and telegram-desktop for the telegram network.

    Carbons
        In the XMPP world, carbons are messages sent from one client to another
        to rapidly synchronize chat history views. In slidge, this refers to
        actions of the user on an official client. They are synchronized between
        official clients and XMPP using carbons.

    XMPP Entity
        An XMPP "recipient", which has a JID. Can be either a
        :class:`slidge.LegacyContact`,
        a :class:`slidge.LegacyMUC`, or
        a :class:`slidge.LegacyParticipant`.

    JID Local Part
        The "username" part of a JID, eg ``username`` in
        ``username@example.org``.

    Avatar
        A picture representing an :term:`XMPP Entity`, such as a profile picture
        for a :class:`slidge.LegacyContact`

    Ad-hoc Command
        A way to interact with the gateway component (or any xmpp entities) via
        a series of forms, to trigger actions or request information.
        See :xep:`0050` for more details.

    Chatbot Command
        A way to interact with the gateway component via chat messages, a bit
        like a shell.

.. rubric:: Footnotes

.. [#f1] "Legacy" may sound weird since XMPP is pretty old now, but this attempts to follow
    the convention of :xep:`0100`
