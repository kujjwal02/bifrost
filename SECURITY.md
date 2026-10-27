# Security

bifrost forwards network ports and opens URLs that a remote machine asks for, so
its defaults assume the remote machine, and the agents on it, may misbehave.

## What bifrost defends against

| Threat | Defence |
| --- | --- |
| Another device on your network reaching remote servers through the router | The router closes every connection whose peer is not loopback before reading a byte. On Linux and WSL it listens on loopback only. |
| A web page using DNS rebinding to read a remote server | The router answers `421` to any `Host` outside `*.localhost`. |
| A prompt-injected agent opening a phishing page on your computer | Pages outside the machine's own servers and files wait for a yes in a popup. More than 5 automatic opens a minute also go to the popup. |
| A web page guessing a file link to read other remote files | File links carry an HMAC of machine and folder, keyed by a secret in the state directory. |
| A file link escaping its folder through `../` or a symlink | The remote resolves the path with `realpath -e` and refuses a file outside the signed folder. Files directly under `/` are never served. |
| A served page's scripts reading other bifrost pages | Every response carries `Content-Security-Policy: sandbox`, so scripts run in an opaque origin. `X-Content-Type-Options: nosniff` too. |
| Crafted output from the remote injecting terminal escapes or commands | Every line from the remote is checked against a strict format. Process names are limited to `[A-Za-z0-9._:-]`. Control characters are stripped before logging. |

## What bifrost does not defend against

- A local process on your computer. Anything running as you can already reach your SSH sockets.
- A remote server you open on purpose. Its page runs in your browser like any site you visit.
- `trusted_peers` entries. Each one lets that address reach every remote server.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/kujjwal02/bifrost/security/advisories/new).
Please do not open a public issue for a vulnerability.
