==========
Privileges
==========

.. note::

  Setting up slidge as a privileged entity (:xep:`0356`) is recommended for the user experience,
  but it can only work of the XMPP user account is on the same server as slidge.

With privileges, slidge can:

- automatically add/remove "puppet contacts" from the XMPP roster of slidge users
- reflect on the XMPP side messages sent by users via a non-XMPP client,
  such official apps of the legacy service
- synchronize other actions done via a non-XMPP client, such as read state, emoji reactions,
  retractions, etc.

Prosody
-------

Installation
~~~~~~~~~~~~

Starting with prosody 0.12, installing the  `mod_privilege <https://modules.prosody.im/mod_privilege.html>`_
community module is as easy as:

.. code-block:: bash

    prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege

Configuration
~~~~~~~~~~~~~

In ``prosody.cfg.lua``, add ``mod_privilege`` to the ``modules_enabled`` list.

Define the gateway component's privileges in the appropriate virtualhost block:

.. code-block:: lua

    VirtualHost "example.com"
      privileged_entities = {
        ["superduper.example.com"] = {
          roster = "both";
          message = "outgoing";
        }
      }

Then either restart the prosody server, or reload config.
You might need to use
`mod_reload_component <https://modules.prosody.im/mod_reload_components.html>`_,
and activate/deactivate hosts
for all changes to be taken into account
(restarting prosody is the easiest way to go).

ejabberd
--------

.. warning::

  While this configuration is the correct way to go, this actually serves no
  purpose in slidge, because of these two issues:
  https://github.com/processone/ejabberd/issues/3990 and
  https://github.com/processone/ejabberd/issues/3942

.. code-block:: yaml

    acl:
      slidge_acl:
        server:
          - "superduper.example.com"

    access_rules:
      slidge_rule:
        - allow: slidge_acl

    modules:
      mod_privilege:
        roster:
          both: slidge_rule
        message:
          outgoing: slidge_rule
      mod_roster:
        versioning: true