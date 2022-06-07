Telegram
========

Gateway registration
--------------------

If you already have a telegram account, go to https://my.telegram.org/apps
to get an API_ID and API_HASH and use them in your gateway registration form.
It should be possible to register a new telegram account via the gateway, but this
has not been tested.

Contacts
--------

On login, your contacts should automatically be added in your roster as members
of the group "Telegram".
The JID of your contacts will be their telegram ID @ the component JID.

To add a contact by their phone number, use jabber search (:xep:`0055`).
