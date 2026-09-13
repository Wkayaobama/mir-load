-- mr-load WezTerm module: Ubuntu terminals (local + remote) and a status pane.
--
-- Install: copy this file next to your wezterm.lua (WezTerm adds that
-- directory to package.path), then in wezterm.lua:
--
--   local wezterm = require 'wezterm'
--   local config = wezterm.config_builder()
--   require('mr-load').apply(config, {
--     repo        = '/home/<you>/mir-load',   -- Linux path as seen INSIDE Ubuntu (WSL/VM/Docker)
--     wsl_domain  = 'WSL:Ubuntu',             -- Windows only; `wsl -l` shows the distro name
--     vm_ssh_host = 'mrload-vm',              -- Host alias from ~/.ssh/config (docs/WEZTERM_UBUNTU.md)
--   })
--   return config
--
-- Then: Launcher menu (Ctrl+Shift+L by default in this module) → pick an entry.
local wezterm = require 'wezterm'
local act = wezterm.action
local M = {}

function M.apply(config, opts)
  opts = opts or {}
  local repo = opts.repo or (wezterm.home_dir .. '/mir-load')
  local is_windows = wezterm.target_triple:find('windows') ~= nil
  local wsl_domain = opts.wsl_domain or 'WSL:Ubuntu'
  local vm_host = opts.vm_ssh_host or 'mrload-vm'

  -- Local Ubuntu on Windows = WSL2 domain; make it the default so every new
  -- tab is already an Ubuntu bash with the repo's tooling.
  if is_windows then
    config.wsl_domains = wezterm.default_wsl_domains()
    config.default_domain = wsl_domain
  end

  local function in_ubuntu(cmd)
    -- run `cmd` in a login bash inside the Ubuntu domain, then keep the shell
    local entry = { args = { 'bash', '-lc', 'cd ' .. repo .. ' 2>/dev/null; ' .. cmd .. '; exec bash -l' } }
    if is_windows then entry.domain = { DomainName = wsl_domain } end
    return entry
  end

  config.launch_menu = config.launch_menu or {}
  local entries = {
    { label = 'mr-load ▸ Ubuntu shell (local: WSL / native)',
      args = in_ubuntu('source .venv/bin/activate 2>/dev/null || true').args,
      domain = in_ubuntu('').domain },
    { label = 'mr-load ▸ bootstrap this machine (apt, gcloud, venv, tests, rehearsal)',
      args = in_ubuntu('bash scripts/bootstrap_ubuntu.sh').args,
      domain = in_ubuntu('').domain },
    { label = 'mr-load ▸ status pane (ledger + latest log, refresh 5 s)',
      args = in_ubuntu('scripts/dev/watch_status.sh').args,
      domain = in_ubuntu('').domain },
    { label = 'mr-load ▸ Docker Ubuntu (local, any OS)',
      args = { 'bash', '-lc', 'cd ' .. repo .. ' && scripts/ubuntu_shell.sh' } },
    { label = 'mr-load ▸ Cloud Shell (remote, preauthenticated gcloud/bq)',
      args = { 'gcloud', 'cloud-shell', 'ssh', '--authorize-session' } },
    { label = 'mr-load ▸ runner VM over IAP (remote, ~/.ssh/config Host ' .. vm_host .. ')',
      args = { 'ssh', vm_host } },
  }
  for _, e in ipairs(entries) do table.insert(config.launch_menu, e) end

  config.keys = config.keys or {}
  table.insert(config.keys, { key = 'L', mods = 'CTRL|SHIFT', action = act.ShowLauncherArgs { flags = 'LAUNCH_MENU_ITEMS' } })
  -- Ctrl+Shift+S: split a status pane to the right (30 %) inside the Ubuntu domain
  local split = { args = in_ubuntu('scripts/dev/watch_status.sh').args, size = 0.3 }
  if is_windows then split.domain = { DomainName = wsl_domain } end
  table.insert(config.keys, { key = 'S', mods = 'CTRL|SHIFT', action = act.SplitPane { direction = 'Right', command = split, size = { Percent = 30 } } })

  -- Remote domain (optional): `wezterm connect mrload-vm` / new tab in that domain.
  config.ssh_domains = config.ssh_domains or {}
  table.insert(config.ssh_domains, { name = 'mrload-vm', remote_address = vm_host, multiplexing = 'None', assume_shell = 'Posix' })
  return config
end

return M
