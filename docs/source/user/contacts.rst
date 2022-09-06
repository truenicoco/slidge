Finding legacy contacts
=======================

After registration, slidge should add your contacts puppet XMPP accounts to your
roster.
If you want to message someone that was not automagically added by slidge, you can guess
their puppet JIDs when the username part is trivial, such as a phone number or
``name.surname``.
In case you don't know the username part of someone, you can use slidge's
search feature, either with Jabber Search (:xep:`0055`) if your client support it,
or via the "find XXX" chat command (direct message to the gateway, similar to the
fallback registration workflow).

.. note::
  Currently, slidge `does not provide <https://todo.sr.ht/~nicoco/slidge/28>`_
  a "friend request workflow", ie, adding/removing legacy contacts to your roster
  does not trigger anything on the legacy network side.
  Use official clients to add/remove contacts to your legacy roster.
  This only applies to network where there is
  such notion though, such as facebook, discord, skype and steam.
