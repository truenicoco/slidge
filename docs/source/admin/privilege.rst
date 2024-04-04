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
- automatically add XMPP bookmarks (:xep:`0402`) for MUCs (:xep:`0045`)

Privileges with Prosody
-----------------------

mod_privilege installation
~~~~~~~~~~~~~~~~~~~~~~~~~~

Starting with prosody 0.12, installing the  `mod_privilege <https://modules.prosody.im/mod_privilege.html>`_
community module is as easy as:

.. code-block:: bash

    prosodyctl install --server=https://modules.prosody.im/rocks/ mod_privilege

Privileges configuration
~~~~~~~~~~~~~~~~~~~~~~~~

In ``prosody.cfg.lua``, add ``mod_privilege`` to the ``modules_enabled`` list.

Define the gateway component's privileges in the appropriate virtualhost block:

.. code-block:: lua

    VirtualHost "example.org"
      privileged_entities = {
        ["superduper.example.org"] = {
          roster = "both";
          message = "outgoing";
          iq = {
            ["http://jabber.org/protocol/pubsub"] = "both";
            ["http://jabber.org/protocol/pubsub#owner"] = "set";
          };
        }
      }

Then either restart the prosody server, or reload config.
You might need to use
`mod_reload_component <https://modules.prosody.im/mod_reload_components.html>`_,
and activate/deactivate hosts
for all changes to be taken into account
(restarting prosody is the easiest way to go).

Privileges with ejabberd
------------------------

.. warning::

  If you want to set up privileges, you need ejabberd with version 23.10 or newer because of these two issues:
  https://github.com/processone/ejabberd/issues/3990 and
  https://github.com/processone/ejabberd/issues/3942

.. code-block:: yaml

    acl:
      slidge_acl:
        server:
        # Make sure to include all of your slidge bridges that need privileges here:
          - "superduper.example.org"
          - "other-walled-garden.example.org"

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
