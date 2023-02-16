Tutorial: implementing a plugin
===============================

Wanna write a new "legacy chat network" slidge plugin? You've come to the right place.

Minimal example
---------------

Let's say we want to create a gateway to the famous *super duper chat network*.
Put this in a file called ``superduper.py``:

.. code-block:: python

    import super_duper.api  # great python lib!
    from super_duper.client import SuperDuperClient

    from slidge import *


    class Gateway(BaseGateway):
        COMPONENT_NAME = "Gateway to the super duper chat network"


    class Session(BaseSession):
        def __init__(self, user: GatewayUser):
            super().__init__(user)
            self.legacy = SuperDuperClient(
                login=self.user.registration_form["username"],
                password=self.user.registration_form["password"],
            )
            self.legacy.add_event_handler(
                callback=self.incoming_legacy_message,
                event=super_duper.api.IncomingMessageEvent
            )

        async def login():
            await self.legacy.login()

        async def incoming_legacy_message(self, msg: super_duper.api.Message):
            contact = await self.contacts.by_legacy_id(msg.sender)
            contact.send_text(msg.text)

        async def send_text(self, chat: Recipient, text: str, *kwargs):
            self.legacy.send_message(text=text, destination=chat.legacy_id)


This can now be launched using ``slidge --legacy-network=superduper --server=...``

The gateway component
*********************

Let's dissect this a bit:

.. code-block:: python

    class Gateway(BaseGateway):
        COMPONENT_NAME = "Gateway to the super duper chat network"

By subclassing :class:`slidge.BaseGateway` we can customize our gateway component in
various ways. Here we just changed its name (something we **have** to do), but
we could also change the registration form fields by overriding
:py:attr:`slidge.BaseGateway.REGISTRATION_FIELDS`, among other things.

The legacy session
******************

Setup
~~~~~

.. code-block:: python

    class Session(BaseSession):
        def __init__(self, user: GatewayUser):
            super().__init__(user)
            self.legacy = SuperDuperClient(
                login=self.user.registration_form["username"],
                password=self.user.registration_form["password"],
            )
            self.legacy.add_event_handler(
                callback=self.incoming_legacy_message,
                event=super_duper.api.IncomingMessageEvent
            )

The session represents the gateway user's session on the legacy network.
To add custom attributes to it, override the ``__init__`` without changing its
signature and do not forget to call the base class ``__init__``.
The :py:attr:`slidge.Session.user` attribute is a :class:`slidge.GatewayUser` instance and
can be used to access the fields that the user filled when subscribing to the gateway,
via :py:attr:`slidge.GatewayUser.registration_form` dict.

Here, we added a ``legacy`` attribute to the session instance, because our fake
superduper lib is coded this way. YMMV depending on the library you use. Good
python libs provide an event handler mechanism similar to what you see here.

Login
~~~~~

.. code-block:: python

        async def login(self):
            await self.legacy.login()

When the gateway user is logged, this method is called on its :py:attr:`slidge.Session.user`
instance. With the superduper library, starting to receive incoming messages is
very convenient, as you can see.

From legacy to XMPP
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

        async def incoming_legacy_message(self, msg: super_duper.api.Message):
            contact = await self.contacts.by_legacy_id(msg.sender)
            contact.send_text(msg.body, legacy_msg_id=msg.id)

We are really lucky, superduper user IDs can directly be mapped to the user part
of a JID. We can just use our session's virtual legacy roster to retrieve a
:class:`slidge.LegacyContact` instance. Just by calling :meth:`slidge.LegacyContact.send_text`,
we effectively transported the message's text to the gateway user. Ain't that great?

From XMPP to legacy
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

        async def send_text(self, chat: Recipient, text: str, *kwargs):
            self.legacy.send_message(text=text, destination=chat.legacy_id)

When our user sends a message to ``something@superduper.example.org``,
this method is automagically called, allowing us to transmit the message to the legacy network.

Going further (WIP)
-------------------

- Adding a contact to the user's roster and setting its name, avatar, ...
- Handling legacy user IDs that are not valid JID user part
- Attachments
- Groupchats (some day...)
