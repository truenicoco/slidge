Dev doc: creating a legacy client
=================================

Making a legacy client requires:

- subclassing :class:`slidge.api.BaseLegacyClient` and :class:`slidge.api.BaseGateway`
- accessing :class:`slidge.api.Buddy` and :class:`slidge.api.LegacyMuc`
  instances via the :class:`slidge.session.Sessions` singleton `sessions`
  that can be imported from :mod:`slidge.api`.

The `sessions` can return the `Session` of a gateway user by its legacy id.
A user's buddies and the MUCs are accessible via their legacy_id using `Session.buddies.by_legacy_id(str)`
and `Session.mucs.by_legacy_id(str)`.

.. automodule:: slidge.api
   :members:
