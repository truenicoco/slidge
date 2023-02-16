===========
Attachments
===========

In order to receive file attachments via slidge, you have two options:

- **No upload**: serve static files from a folder via an HTTP server (eg nginx,
  prosody's `http_files <https://prosody.im/doc/modules/mod_http_files>`_, etc.)
- **HTTP File Upload** (:xep:`0363`): for certain unusual setups, might be slightly easier
  to setup

No upload
=========

At the minimum, you need to set up ``no-upload-path`` to a local directory,
``no-upload-url-prefix`` to an URL prefix pointing to files in that directory
(see :ref:`Configuration` for details on how to set these options).

Example: if you set ``no-upload-path=/var/lib/slidge/attachments/`` and
``no-upload-url-prefix=https://www.example.com/slidge/``, a file
``/var/lib/slidge/attachments/file.txt`` should be downloadable at the
``https://www.example.com/slidge/file.txt`` public URL.

Make sure that ``no-upload-path`` is writeable by slidge and readable by
your HTTP server. You may use ``no-upload-read-others=true`` to do that easily,
but you might want to restrict which users can read this directory.

.. warning::

  Slidge will not take care of removing old files, so you should set up a cronjob,
  a systemd timer, or something similar, to regularly delete files, eg.
  ``find . -mtime +7 -delete && find . -depth -type d -empty -delete``
  to clean up files older than a week.

Example 1: prosody's http_files
-------------------------------

Here, ``no-upload-url-prefix`` would be ``https://example.com:5281/files/``,
as per the `mod_http_files documentation <https://prosody.im/doc/modules/mod_http_files>`_.

.. code-block:: lua

    modules_enabled = {
      -- Other modules
      "http_files";
    }

    http_files_dir = "/var/lib/slidge/attachments" -- Point it to a path to serve


Example 2: nginx
----------------

Here, ``no-upload-url-prefix`` would be ``http://example.com/slidge/``.

.. code-block:: nginx

    server {
      listen 80;
      server_name example.com;
      root /var/www/html;  # if you already have nginx serving files…

      # the section below if for slidge
      location /slidge {
        alias /var/lib/slidge/attachments/;
      }
    }

HTTP File Upload
================

This was slidge only option up to v0.1.0rc1, but is now *not* recommended.
You need to manually set the JID of the upload service that slidge must use, eg
``upload-service=upload.example.com`` (see :ref:`Configuration`).
Slidge will upload files to your upload component, just like you do from your
normal account, whenever you share a file via :xep:`0363`.

Pros:

- does not require slidge to be on the same host as the XMPP server
- might be easier to set up (works out-of-the-box-ish on prosody)
- upload components generally handle quotas and cleaning of old files

Cons:

- more resource usage (using HTTP to transfer files on the same machine is
  a waste of resources)

Example 1: prosody's mod_http_file_share
----------------------------------------

If you use prosody's `mod_http_file_share <https://prosody.im/doc/modules/mod_http_file_share>`_,
you probably don't need any extra configuration, but slidge might run out of quota pretty fast.

Example 2: ejabberd mod_http_upload
-----------------------------------

ejabberd's HTTP upload will not let the component directly request upload slots,
so you need to use a pseudo user on the component domain, eg,
``slidge@superduper.example.com`` and use slidge's
``--upload-requester=slidge@superduper.example.com`` option.

.. code-block:: yaml

    acl:
      slidge_acl:
        server:
          - "superduper.example.com"

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
