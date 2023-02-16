modules_enabled = {
  -- Other modules [...]
  "http_files";      -- for the "no upload" option for attachments
  "privilege";
  "http_file_share"; -- for the "upload" option for attachments (most likely already present in your setup)
}

-- if you want to use the "no upload" option with
http_files_dir = "/var/lib/slidge/attachments" -- Point it to a path to serve

VirtualHost "example.com"
  privileged_entities = {  -- for privileges
    ["superduper.example.com"] = {
      roster = "both";
      message = "outgoing";
    }
  }

Component "superduper.example.com"
  component_secret = "secret"      -- replace this with a real secret!
  modules_enabled = {"privilege"}  -- for privileges