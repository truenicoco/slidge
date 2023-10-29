Glossary
========



.. glossary::
    :sorted:

    User
        Someone using slidge, ie, someone who has an XMPP account and registered
        to a slidge-based XMPP component.

    Legacy Network
        The messaging network slidge (and the :term:`User`) communicates with.

    Legacy Module
        An XMPP gateway based on slidge.

    Legacy Contact
        Someone using the legacy network to communicate with the :term:`User`.

    Official Client
        The reference client(s) for a legacy network. Examples: telegram-android
        and telegram-desktop for the telegram network.

    Carbons
        In the XMPP world, carbons (:xep:`0280`) are messages sent by the XMPP
        server to keep outgoing chat history in sync between different clients
        connected to the same XMPP account (eg, a desktop and mobile app).
        In slidge however, this refers to actions of the :term:`User` done from
        an :term:`Official client`.

    XMPP Entity
        Someone or something that has a JID, such as
        an XMPP user account (eg, ``someone@example.org``),
        an XMPP component (eg, ``slidge.example.org``),
        an XMPP Multi-User Chat (MUC in short,
        eg ``cool-group@groups.example.org``),
        an XMPP server (eg, ``example.org``),
        … basically anything that has a JID.

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

    Command
        Either an :term:`Ad-hoc Command` or a :term:`Chatbot Command`.
        Slidge provides the same commands via both interfaces, so they can be
        used on any client.

    Roster
        This is how the "contact list" is called in XMPP.

"Legacy" may sound weird since XMPP is pretty old now, but slidge follows
the convention of :xep:`0100`.
