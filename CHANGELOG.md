# Changelog

## 1.0.0

First public release.

- Every port a saved machine listens on opens at `http://<port>.<machine>.localhost`, IPv4 and IPv6, WebSockets included.
- `ferry forward <machine>:<port>` adds a same-port `localhost:<port>` forward for OAuth callbacks and https dev servers.
- URLs a machine opens come to this browser: its own pages and files at once, other pages after a yes in a popup.
- Remote files open through signed links at `http://files.<machine>.localhost`, with relative links working.
- Ctrl+click on a `http://localhost:<port>` link in herdr opens it on the machine herdr shows.
- `ferry setup <machine>` installs the remote opener; `ferry install-cli` puts `ferry` on PATH.
- The next workspace switch restarts the daemon after a crash or a `herdr plugin update`, and its log stays under 4 MB.
