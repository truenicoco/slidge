Facebook messenger
------------------

.. warning::
  Facebook is very much not OK with you using something else than their spyware to exchange messages
  with their data slaves (yes, you too are meta's data slave, that's right).
  All sort of bad things may happen to your facebook account if you use slidge.
  You are seriously advised NOT to use slidge with a facebook account that serves other purposes
  than stalking your exes and/or making fun of your QAnon-brainwashed auntie, since it may
  get locked at any point. See :ref:`Keeping a low profile` and the
  `maufbabi docs <https://docs.mau.fi/bridges/python/facebook/authentication.html>`_ (library used in this plugin)
  if you feel like you can survive this

Empirically, add a phone number for 2FA to your facebook account and you should be fine in case slidge's
login is detected as suspicious by facebook's automated security stuff.

Roster
******

Contact JIDs are of the form ``john.doe123@slidge-facebook.example.org`` where ``john.doe123`` is a
facebook username (also seen in ``https://facebook.com/john.doe123``).

The 2 last facebook friends you interacted with should be added to your roster on slidge's registration.
If you want to find the facebook username of someone using their (more or less) real name,
use slidge's search feature: :ref:`Finding legacy contacts`.

Presences
*********

Not implemented, all your facebook will always appear online in XMPP.
