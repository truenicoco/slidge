modules_enabled = {
  -- [...]
  -- "http_file_share"; -- for attachments with the "upload" option
  "http_files"; -- for attachments with the "no upload" option
  "privilege"; -- for roster sync and 'legacy carbons'
}

-- for attachments with the "no upload" option
-- in slidge's config: no-upload-path=/var/lib/slidge/attachments
http_files_dir = "/var/lib/slidge/attachments"

VirtualHost "example.org"
  -- for roster sync and 'legacy carbons'
  privileged_entities = {
    ["superduper.example.org"] = {roster = "both"; message = "outgoing"},
    ["other-walled-garden.example.org"] = {roster = "both"; message = "outgoing"},
    -- repeat for other slidge plugins…
  }

Component "superduper.example.org"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "other-walled-garden.example.org"
  component_secret = "some-other-secret"
  modules_enabled = {"privilege"}

-- repeat for other slidge plugins…
  
-- -- for attachments with the "upload" option
-- -- in slidge's config: upload-service=upload.example.org
-- Component "upload.example.org" "http_file_share"