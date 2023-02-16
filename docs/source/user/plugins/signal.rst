Signal
------

.. note::
  Nothing in the signal ToS prevents you from using third-party signal clients.
  The signal plugin uses `signald <https://signald.org/>`_ to interact with the signal network, which
  self advertises as
  `"not nearly as secure as the real Signal clients" <https://gitlab.com/signald/signald/-/issues/101>`_,
  now you're warned.

Roster
******

If you link your signal account to a "primary" signal device (eg, the official android signal app),
your contacts should be added to your roster on slidge registration.

Contact JIDs are of the form ``<UUID>@slidge-signal.example.org``.
To search for a UUID using a phone number, use the dedicated search command or the ``find``
chat command.
More info: :ref:`Finding legacy contacts`.

Presences
*********

There is no notion of presence in signal, so contacts of your roster will always appear online.
