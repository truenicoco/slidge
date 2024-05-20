===========
Attachments
===========

In order to receive file attachments via slidge, you have two options:

- **No upload**: serve static files from a folder via an HTTP server (eg nginx,
  prosody's `http_files <https://prosody.im/doc/modules/mod_http_files>`_, etc.)
- **HTTP File Upload** (:xep:`0363`)

No upload
=========

At the minimum, you need to set up no-upload-path to a local directory, no-upload-url-prefix to an URL prefix pointing to files in that directory (see :ref:`Configuration`
for details on how to set these options).

Make sure that ``no-upload-path`` is writeable by slidge and readable by
your HTTP server. You may use ``no-upload-file-read-others=true`` to do that easily,
but you might want to restrict which users can read this directory.

.. warning::

  Slidge will not take care of removing old files, so you should set up a cronjob,
  a systemd timer, or something similar, to regularly delete files, eg.
  ``find . -mtime +7 -delete && find . -depth -type d -empty -delete``
  to clean up files older than a week.

For the following examples, in slidge's config,
you would have ``no-upload-path=/var/lib/slidge/attachments``.

Example 1: prosody's http_files
-------------------------------

Here, ``no-upload-url-prefix`` would be ``https://example.org:5281/files/``,
as per the `mod_http_files documentation <https://prosody.im/doc/modules/mod_http_files>`_.

.. code-block:: lua

    modules_enabled = {
      -- Other modules
      "http_files";
    }

    -- Must be the same value as slidge's no-upload-path
    http_files_dir = "/var/lib/slidge/attachments"


Example 2: nginx
----------------

Here, ``no-upload-url-prefix`` would be ``http://example.org/slidge/``.

.. code-block:: nginx

    server {
      listen 80;
      server_name example.org;
      root /var/www/html;  # if you already have nginx serving files…

      # the section below is for slidge
      location /slidge {
        #  Must be the same value as slidge's no-upload-path
        alias /var/lib/slidge/attachments/;
      }
    }

See the `nginx docs <https://docs.nginx.com/nginx/admin-guide/web-server/serving-static-content/>`_ for more info.

HTTP File Upload
================

This was slidge only option up to v0.1.0rc1, but is now *not* recommended.
You need to manually set the JID of the upload service that slidge must use, eg
``upload-service=upload.example.org`` (see :ref:`Configuration`).
Slidge will upload files to your upload component, just like you do from your
normal account, whenever you share a file via :xep:`0363`.

Pros:

- does not require slidge to be on the same host as the XMPP server
- might be easier to set up (works out-of-the-box-ish on prosody)
- upload components generally handle quotas and cleaning of old files

Cons:

- more resource usage (using HTTP to copy or move files on a single host)

Example 1: prosody's mod_http_file_share
----------------------------------------

In slidge's config: ``upload-service=example.org``

.. code-block:: lua

  Component "upload.example.org" "http_file_share"
    -- max file size: 16 MiB
    http_file_share_size_limit = 16*1024*1024

    -- max per day per slidge component: 100 MiB
    http_file_share_daily_quota = 100*1024*1024

    -- 1 GiB total
    http_file_share_global_quota = 1024*1024*1024

    -- starting from prosody > 0.12 you will need to add one of these two lines:
    -- server_user_role = "prosody:registered"
    -- http_file_share_access = { "superduper.example.org" }

More info: `mod_http_file_share <https://prosody.im/doc/modules/mod_http_file_share>`_.

Example 2: ejabberd mod_http_upload
-----------------------------------

ejabberd's HTTP upload will not let the component directly request upload slots,
so you need to use a pseudo user on the component domain, (eg,
``slidge@superduper.example.org``) with Slidge's
``upload-requester=slidge@superduper.example.org`` option.

In slidge's config: ``upload-service=example.org``

The subdomain's FQDN (example.org) should be listed under the top level 'hosts'.

.. code-block:: yaml

    hosts:
      - "example.org"

    acl:
      slidge_acl:
        server:
          - "superduper.example.org"

    listen:
      -
        port: 5443
        module: ejabberd_http
        tls: true
        request_handlers:
          /upload: mod_http_upload

    modules:
      mod_http_upload:
        # Any path that ejabberd has read and write access to
        docroot: /ejabberd/upload
        put_url: "https://@HOST@:5443/upload"
        access:
          - allow: local
          - allow: slidge_acl


To get more information about component configuration, see `ejabberd's docs
<https://docs.ejabberd.im/admin/configuration/modules/#mod-http-upload>`_.
