Configure the XMPP server
=========================

Slidge requires a running and properly configured XMPP server running and accepting
component connections. An upload (XEP:`0363`) component is also required to exchange files
(eg. QR codes) with the gateway and the legacy contacts.

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

Use `mod_privilege <https://modules.prosody.im/mod_privilege.html>`_ to allow slidge to:

- manage the gateway user's roster, i.e., automatically add legacy contacts
- impersonate the user to send XMPP carbons for messages and markers sent by the user
  from the official legacy client

Installation
~~~~~~~~~~~~

Starting with prosody 0.12, installing the module is as easy as:

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


Slidge to display information provided by any chat network use XEP-0363 (HTTP File Upload). To display qrcode or any other element to permit some action as device association. It's required to enable propsody http_file_share plugin

.. code-block:: lua

   Component "upload.example.org" "http_file_share"

To get more informaton about component configuration : https://prosody.im/doc/modules/mod_http_file_share

ejabberd
--------

Add component
*************

Add this block to your ejabberd configuration file, in the ``listen`` section.
Change the port, hostname and secret accordingly.

.. code-block:: yaml

    listen:
      -
        ip: 127.0.0.1
        port: 5233
        module: ejabberd_service
          hosts:
            superduper.example.com:
              password: secret

mod_privilege
*************

This is required to let slidge manage your roster and synchronize your messages
sent from an official client.
Roster management also requires roster versioning.

.. code-block:: yaml

    modules:
      mod_privilege:
        roster:
          both: superduper.example.com
        message:
          outgoing: superduper.example.com
      mod_roster:
        versioning: true

Upload component
****************

Slidge to display information provided by any chat network use XEP-0363 (HTTP File Upload). 
To display qrcode or any other element to permit some action as device association.

.. code-block:: yaml

    listen:
      -
        port: 5443
        module: ejabberd_http
        tls: true
        request_handlers:
          /upload: mod_http_upload

.. code-block:: yaml

    modules:
      mod_http_upload:
        docroot: /ejabberd/upload
        put_url: "https://@HOST@:5443/upload"


To get more informaton about component configuration : https://docs.ejabberd.im/admin/configuration/modules/#mod-http-upload