Configure the XMPP server
=========================

Slidge requires a running and properly configured XMPP server running and accepting
component connections and uses different containers/processes for each gateway.

Slidge uses :xep:`0363` (HTTP File Upload) to receive files from your contacts.
For some networks, this is also required to receive QR codes to scan in official apps.
Chances are you already have this component enabled in your XMPP server config.

Slidge also uses :xep:`0356` (Privileged Entity) to:

- manage the user's roster, i.e., automatically fill it up with legacy contacts
- impersonate the user to keep sent history and read markers in sync if they use
  an official app and not slidge exclusively to send messages on the legacy network.

Prosody
-------

Add component
*************

Add a component block below the appropriate virtualhost in ``prosody.cfg.lua``

.. code-block:: lua

    Component "superduper.example.com"
      component_secret = "secret"  -- replace this with a real secret!
      modules_enabled = {"privilege"}

mod_privilege
*************

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

Then either restart the prosody server, or reload config. You might need to use
`mod_reload_component <https://modules.prosody.im/mod_reload_components.html>`_
for all changes to be taken into account (restarting prosody is the easiest way to go).

Upload component
****************

In prosody the easiest option is to use the
`http_file_share <https://prosody.im/doc/modules/mod_http_file_share>`_ module.

.. code-block:: lua

   Component "upload.example.org" "http_file_share"


ejabberd
--------

Slidge uses different containers/processes for each gateway. Therefore administrators
should setup these steps for each individual gateway. This is because each gateway
makes use of an individual JID (such as telegram.example.com, whatsapp.example.com, etc).
Only exceptions are the ``mod_http_upload``, ``mod_privilege``, ``mod_roster`` and ``access_rules``.
These stay the same for each gateway you add, so there is no need to repeat these steps for new gateways.


Add the slidge component
************************

Add this block to your ejabberd configuration file, in the ``listen`` section.
Change the 'port', 'hosts' and 'secret' accordingly.
Note: The port does not need to be forwarded (opened to the internet) as in this documentation,
we expect slidge to be on the same host as the XMPP server.

.. code-block:: yaml

    listen:
      -
        ip: 127.0.0.1
        port: 5347
        module: ejabberd_service
        hosts:
          superduper.example.com:
            password: secret


.. code-block:: yaml

        hosts:
          superduper.example.com:

The 'hosts' domain can be any given subdomain as long as the domain is pointing to the server's ip running ejabberd.
Example: Telegram.example.com, whatsapp.example.com etc.

The subdomain's FQDN (example.com) should be listed under the top level 'hosts'.
Example:

.. code-block:: yaml

        hosts:
          - "example.com"

These same principles also apply to ACL.

ACL
***

Create an `acl <https://docs.ejabberd.im/admin/configuration/basic/#acl>`_ for the component:

.. code-block:: yaml

    acl:
      slidge_acl:
        server:
          - "superduper.example.com"

Acess Rule
**********

Create an `access_rule <https://docs.ejabberd.im/admin/configuration/basic/#access-rules>`_ for the component:

.. code-block:: yaml

    access_rules:
      slidge_rule:
        - allow: slidge_acl

mod_privilege
*************

Make slidge a "`privileged entity <https://docs.ejabberd.im/admin/configuration/modules/#mod-privilege>`_" and enable roster versioning.

.. code-block:: yaml

    modules:
      mod_privilege:
        roster:
          both: slidge_rule
        message:
          outgoing: slidge_rule          
      mod_roster:
        versioning: true

Upload component
****************

ejabberd's HTTP upload will not let the component directly request upload slots,
so you need to use a pseudo user on the component domain, eg,
``slidge@superduper.example.com`` and use slidge's
``--upload-requester=slidge@superduper.example.com`` option.

.. code-block:: yaml

    listen:
      -
        port: 5443
        module: ejabberd_http
        tls: true
        request_handlers:
          /upload: mod_http_upload

    modules:
      mod_http_upload:
        docroot: /ejabberd/upload     # Can be any path as long as ejabberd has Read and Write access to the directory.
        put_url: "https://@HOST@:5443/upload"
        access:
          - allow: local
          - allow: slidge_acl


To get more information about component configuration, see `ejabberd's docs
<https://docs.ejabberd.im/admin/configuration/modules/#mod-http-upload>`_.
