# Changelog

## 2.1.0

- `bifrost bind <machine>` and `bifrost unbind <machine>` replace `bifrost setup` and `bifrost setup --undo`.

## 2.0.0

Renamed from ferry to bifrost, because two herdr plugins already use the name ferry. Everything that
carried the old name changed, so a ferry 1.0.0 install needs these steps:

- Uninstall `kujjwal02.ferry`, then `herdr plugin install kujjwal02/bifrost`.
- Move `config.json` to the folder `herdr plugin config-dir kujjwal02.bifrost` prints.
- Run `bifrost setup <machine>` again; the opener now lives in `~/.local/share/bifrost/bin`.
- Old file links stop working. The `*.localhost` route URLs stay the same.

## 1.0.0

First public release.

- Every port a saved machine listens on opens at `http://<port>.<machine>.localhost`, IPv4 and IPv6, WebSockets included.
- `bifrost forward <machine>:<port>` adds a same-port `localhost:<port>` forward for OAuth callbacks and https dev servers.
- URLs a machine opens come to this browser: its own pages and files at once, other pages after a yes in a popup.
- Remote files open through signed links at `http://files.<machine>.localhost`, with relative links working.
- Ctrl+click on a `http://localhost:<port>` link in herdr opens it on the machine herdr shows.
- `bifrost setup <machine>` installs the remote opener; `bifrost install-cli` puts `bifrost` on PATH.
- The next workspace switch restarts the daemon after a crash or a `herdr plugin update`, and its log stays under 4 MB.
