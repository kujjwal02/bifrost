# ferry

[![CI](https://github.com/kujjwal02/ferry/actions/workflows/ci.yml/badge.svg)](https://github.com/kujjwal02/ferry/actions/workflows/ci.yml)
[![herdr](https://img.shields.io/badge/herdr-%E2%89%A50.9.0-cba6f7)](https://herdr.dev/plugins/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

A [herdr](https://herdr.dev) plugin for agents on remote machines. Their dev servers, the links they open
and the files they write show up in the browser on the computer you sit at, with nothing to forward by hand.

![What ferry does: a remote dev server, a link, a file and a Ctrl+clicked localhost link each open in the local browser](docs/ferry.svg)

ferry reuses the SSH details herdr already has. It needs no server, account or VPN, and installs nothing
on the remote unless you run `ferry setup`. [How it works](docs/architecture.md) has the design and diagrams.

## Install

You need herdr 0.9.0 or newer, Python 3.10 or newer, and OpenSSH, on macOS or Linux (WSL is experimental).
Each remote must be Linux, saved in herdr as a [machine](https://herdr.dev/docs/remote/) that logs in with a
key and no prompt.

```sh
herdr plugin install kujjwal02/ferry
herdr plugin action invoke kujjwal02.ferry.install-cli   # optional: `ferry` in ~/.local/bin
ferry setup afk                                          # once per machine, for links and files
```

The daemon starts with herdr. Bind the status popup to a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "kujjwal02.ferry.status"
description = "ferry ports"
```

## Ports

Every port a machine listens on opens about a second later at `http://<port>.<machine>.localhost`.
`<machine>` is the herdr machine label, so `herdr machine rename <id> --label afk` gives `*.afk.localhost`.
Two machines can both run port 3000. WebSockets and hot reload work, and browsers resolve `*.localhost`
without DNS. `http://localhost/` lists every route.

ferry routes listeners that belong to your SSH user, on loopback or a wildcard address, port 1024 or higher.
For a tool that insists on `localhost:<port>`, such as an OAuth callback or an https dev server, run
`ferry forward afk:1455`.

## Links the remote opens

![gh auth login on afk opens a herdr popup asking to open github.com/login/device](docs/links.svg)

After `ferry setup afk`, a link that an agent opens on afk opens here. `xdg-open`, `$BROWSER`, `gh` and
the npm `open` package all go through it.

- The machine's own pages (`http://localhost:<port>`) and files open at once.
- Any other page waits for a yes in a herdr popup, so a prompt-injected agent cannot open a phishing page
  on your computer without you. Press Enter to open, `n` to skip, `q` to skip all.
- More than 5 automatic opens in a minute also go to the popup.

## Remote files

![xdg-open report.html on afk opens the report, with its charts, at files.afk.localhost](docs/files.svg)

`xdg-open report.html` on afk, or `ferry open afk:~/proj/report.html` here, opens the file with its CSS
and images. Each reload reads the file again. Links are signed and stay inside their folder; see
[SECURITY.md](SECURITY.md).

## Ctrl+click in herdr

![Ctrl+click on http://localhost:5173 in a remote pane opens afk's server](docs/click.svg)

Ctrl+click a `http://localhost:5173` link in a remote pane, and ferry opens afk's server, not whatever
listens on this computer's port 5173.

## Commands

| Command | Does |
| --- | --- |
| `ferry status` | list machines, routed ports and their URLs |
| `ferry setup <machine>` / `--undo` | install or remove the opener on a machine |
| `ferry open <machine>:<path>` | open a remote file in this browser |
| `ferry forward <machine>:<port>` / `--stop` | add or remove a same-port `localhost:<port>` forward |
| `ferry logs [-f]` | show or follow the daemon log |
| `ferry restart` | reconnect every machine |

`ferry --help` lists the rest.

## Configuration

Optional. Put `config.json` in the folder `herdr plugin config-dir kujjwal02.ferry` prints, then run
`ferry restart`:

```json
{"min_port": 3000, "ignored_ports": [9119], "router_port": 8000, "log_level": "debug"}
```

| Key | Default | Meaning |
| --- | --- | --- |
| `min_port` | `1024` | lowest port to route |
| `ignored_ports` | `[]` | ports never routed |
| `ignored_process_prefixes` | editor and sync helpers | process names never routed |
| `router_port` | `80` | the router's port; any other value appears in URLs |
| `log_level` | `info` | `debug`, `info` or `warning` |
| `trusted_peers` | `[]` | extra addresses the router accepts, for WSL |

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| a machine shows `retrying` | `ssh <target> true` must log in without a prompt |
| a port is missing | the server must listen on loopback or `0.0.0.0` as your user |
| `http://localhost/` does not load | another program holds port 80; set `router_port` |
| links on the remote open there | run `ferry setup <machine>`, then restart the remote shell and agent |
| the daemon is down | `ferry status` prints the last crash; `ferry logs` has the rest |

## Uninstall

```sh
ferry setup --undo afk          # for each machine you set up
ferry stop
herdr plugin uninstall kujjwal02.ferry
rm ~/.local/bin/ferry
```

## Credits

The connection design follows [herdr-autofwd](https://github.com/entrptaher/herdr-autofwd) and
[herdr-portfwd](https://github.com/miko-misa/herdr-portfwd). Licensed [MIT](LICENSE).
