"""bifrost: bring herdr saved machines to this computer's browser.

Every port a machine listens on opens at http://<port>.<machine>.localhost.
URLs and files the machine opens (remote-open.sh) open here, files served from
the machine at http://files.<machine>.localhost. Run `bifrost --help` for commands.
Stdlib only, so the plugin needs no build step on any device. Start it through
the `bifrost` launcher, which picks a Python new enough for `match`.
"""

import asyncio
import collections
import enum
import faulthandler
import fcntl
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import logging.handlers
import mimetypes
import os
import posixpath
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import termios
import threading
import time
import tty
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

PLUGIN_ID = "kujjwal02.bifrost"
PLUGIN_ROOT = Path(__file__).resolve().parent
REPOSITORY_URL = "https://github.com/kujjwal02/bifrost"
HERDR = os.environ.get("HERDR_BIN_PATH") or shutil.which("herdr") or "herdr"
# Same defaults as herdr 0.9, so `bifrost status` in a plain shell reads the files
# of the daemon that herdr started.
XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
STATE_DIR = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", XDG_STATE_HOME / "herdr/plugins" / PLUGIN_ID))
CONFIG_DIR = Path(os.environ.get("HERDR_PLUGIN_CONFIG_DIR", Path.home() / ".config/herdr/plugins/config" / PLUGIN_ID))
CONFIG_FILE = CONFIG_DIR / "config.json"
STATUS_FILE = STATE_DIR / "status.json"
LOCK_FILE = STATE_DIR / "daemon.lock"
PID_FILE = STATE_DIR / "daemon.pid"
LOG_FILE = STATE_DIR / "daemon.log"
CRASH_LOG_FILE = STATE_DIR / "crash.log"
PENDING_OPENS_FILE = STATE_DIR / "pending-opens.json"
CONFIRM_LOCK_FILE = STATE_DIR / "confirm.lock"
FILE_LINK_KEY_FILE = STATE_DIR / "file-link.key"
CLI_SHIM_RECORD_FILE = STATE_DIR / "cli-shim-path"
DEFAULT_HERDR_SOCKET = Path.home() / ".config/herdr/herdr.sock"
# macOS caps a unix socket path at 104 bytes; the state dir alone is close to that.
SOCKET_DIR = Path.home() / ".ssh/sockets"

MACHINE_POLL_S = 2
TICK_S = 1
RETRY_MAX_S = 60
HEALTHY_CONNECTION_S = 60
HERDR_MISSED_CHECKS_MAX = 3
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
REQUEST_HEAD_MAX_BYTES = 64 * 1024
REQUEST_HEAD_TIMEOUT_S = 10
COPY_CHUNK_BYTES = 64 * 1024
FILE_READ_TIMEOUT_S = 30
COMMAND_TIMEOUT_S = 10
FAILED_FORWARD_RETRY_S = 5
OPEN_FORWARD_WAIT_S = 5
AUTO_OPENS_PER_MINUTE = 5
PENDING_OPEN_MAX_AGE_S = 600
CONFIRM_POPUP_RETRY_S = 5
NOTIFICATION_GAP_S = 10
REMOTE_NAME_MAX_CHARS = 64
REMOTE_URL_MAX_CHARS = 300
DEFAULT_MIN_PORT = 1024
DEFAULT_ROUTER_PORT = 80
LINUX_FALLBACK_ROUTER_PORT = 8080
# These listeners belong to editor and sync plumbing, never a page to browse.
DEFAULT_IGNORED_PROCESS_PREFIXES = ["code-", "cursor-", "windsurf-", "syncthing"]
LOOPBACK_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
# Hosts that mean "the machine itself" in a URL the machine asks to open.
REMOTE_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}
# DNS rebinding cannot produce these Host values, so they are safe to answer.
INDEX_HOSTS = {"localhost", "bifrost.localhost", "127.0.0.1", "[::1]"}
ROUTE_HOST = re.compile(r"^(\d{1,5})\.([a-z0-9-]+)\.localhost$")
FILES_HOST = re.compile(r"^files\.([a-z0-9-]+)\.localhost$")
LISTENER_TOKEN = re.compile(r"^(\d{1,5})/([46])/([A-Za-z0-9._:-]{1,32})$")
OPEN_REQUEST = re.compile(r"^(?:(?:https?|file)://[^\s\x00-\x1f\x7f-\x9f]+|/[^\x00-\x1f\x7f-\x9f]*)$")
FILE_LINK_SIGNATURE = re.compile(r"^[0-9a-f]{24}$")
# Opaque origin: a served page runs its scripts but cannot read other bifrost
# pages, routes, or cookies, even though it loads from a *.localhost origin.
FILE_PAGE_POLICY = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"
# Prints `ok SIZE` then the file, only for a regular file inside the folder the
# link was signed for. realpath runs remotely, so a symlink cannot lead out.
REMOTE_FILE_SCRIPT = """
folder=$(realpath -e -- "$1") && file=$(realpath -e -- "$2") || exit 4
[ "$folder" != / ] || exit 5
case $file in "$folder"/*) ;; *) exit 5 ;; esac
[ -f "$file" ] || exit 4
printf 'ok %s\\n' "$(stat -c %s -- "$file")"
exec cat -- "$file"
"""
# Both shims carry these lines, so bifrost finds its own copies and never
# overwrites or runs a file it did not write.
REMOTE_OPENER_MARKER = "# bifrost remote opener"
CLI_SHIM_MARKER = "# bifrost command shim"
REMOTE_OPENER_DIR = "$HOME/.local/share/bifrost/bin"
# Writes the remote opener from stdin. "refresh" only replaces a copy that
# `bifrost setup` installed, so connecting never installs anything by itself.
REMOTE_OPENER_INSTALL_SCRIPT = f"""
set -e
dir="{REMOTE_OPENER_DIR}"
[ "$1" = create ] || [ -f "$dir/xdg-open" ] || exit 0
mkdir -p "$dir"
cat > "$dir/.xdg-open.partial"
chmod 755 "$dir/.xdg-open.partial"
mv -f "$dir/.xdg-open.partial" "$dir/xdg-open"
"""
SHELL_SETUP_BEGIN = "# >>> bifrost >>>"
SHELL_SETUP_END = "# <<< bifrost <<<"
# Adds or removes the PATH and BROWSER lines in the rc file of the login
# shell. A symlinked rc file belongs to a dotfiles manager, so it is left alone.
REMOTE_SHELL_SETUP_SCRIPT = f"""
set -e
case $(basename "${{SHELL:-sh}}") in
  zsh) rc="$HOME/.zshenv" ;;
  bash) rc="$HOME/.bashrc" ;;
  *) rc="$HOME/.profile" ;;
esac
if [ "$1" = remove ]; then
  rm -f "$HOME/.local/share/bifrost/bin/xdg-open"
  rmdir "$HOME/.local/share/bifrost/bin" "$HOME/.local/share/bifrost" 2>/dev/null || :
  if [ -L "$rc" ]; then echo "symlink $rc"; exit 0; fi
  if [ -f "$rc" ] && grep -qF '{SHELL_SETUP_BEGIN}' "$rc"; then
    sed '/^{SHELL_SETUP_BEGIN}/,/^{SHELL_SETUP_END}/d' "$rc" > "$rc.bifrost-partial"
    cat "$rc.bifrost-partial" > "$rc" && rm -f "$rc.bifrost-partial"
    echo "removed $rc"
  fi
  exit 0
fi
if [ -L "$rc" ]; then echo "symlink $rc"; exit 0; fi
if [ -f "$rc" ] && grep -qF '{SHELL_SETUP_BEGIN}' "$rc"; then echo "present $rc"; exit 0; fi
cat >> "$rc" <<'BLOCK'
{SHELL_SETUP_BEGIN} added by `bifrost setup`; `bifrost setup --undo` removes it
case ":$PATH:" in *":{REMOTE_OPENER_DIR}:"*) ;; *) export PATH="{REMOTE_OPENER_DIR}:$PATH" ;; esac
export BROWSER="{REMOTE_OPENER_DIR}/xdg-open"
{SHELL_SETUP_END}
BLOCK
echo "added $rc"
"""
REMOTE_FILE_MISSING_EXIT = 4
REMOTE_FILE_OUTSIDE_EXIT = 5

logger = logging.getLogger("bifrost")


def printable(text, limit=REMOTE_NAME_MAX_CHARS):
    """Remote text with control characters replaced, so it cannot drive the
    terminal (OSC 52 writes the clipboard) or forge log lines."""
    cleaned = "".join(c if c.isprintable() else "?" for c in str(text))
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


class LogLevel(enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"


class Platform(enum.Enum):
    MACOS = "macos"
    WSL = "wsl"
    LINUX = "linux"


def local_platform():
    if sys.platform == "darwin":
        return Platform.MACOS
    osrelease = Path("/proc/sys/kernel/osrelease")
    if os.environ.get("WSL_DISTRO_NAME") or (osrelease.exists() and "microsoft" in osrelease.read_text().lower()):
        return Platform.WSL
    return Platform.LINUX


PLATFORM = local_platform()


def open_locally(url):
    """Open a URL in the browser of the computer you sit at."""
    match PLATFORM:
        case Platform.MACOS:
            command = ["open", url]
        case Platform.WSL:
            # WSL has no browser of its own; the Windows default one opens it.
            command = ["wslview", url] if shutil.which("wslview") else ["explorer.exe", url]
        case Platform.LINUX:
            command = [system_xdg_open(), url]
    logger.info("opening %s", printable(url, REMOTE_URL_MAX_CHARS))
    subprocess.run(command, capture_output=True, timeout=COMMAND_TIMEOUT_S)


def system_xdg_open():
    # On a Linux box that is itself a watched machine, the xdg-open on PATH may
    # be the remote opener, which would queue the URL instead of opening it.
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / "xdg-open"
        if candidate.is_file() and os.access(candidate, os.X_OK) and not is_remote_opener(candidate):
            return str(candidate)
    return "/usr/bin/xdg-open"


def is_remote_opener(path):
    try:
        with open(path, "rb") as script:
            return REMOTE_OPENER_MARKER.encode() in script.read(512)
    except OSError:
        return False


def plugin_version():
    manifest = (PLUGIN_ROOT / "herdr-plugin.toml").read_text()
    return re.search(r'^version = "([^"]+)"', manifest, re.MULTILINE).group(1)


def effective_router_port(requested_port):
    """The port the router can bind as a normal user on this computer."""
    match PLATFORM:
        case Platform.MACOS:
            return requested_port
        case Platform.WSL | Platform.LINUX:
            limit_file = Path("/proc/sys/net/ipv4/ip_unprivileged_port_start")
            first_unprivileged_port = int(limit_file.read_text()) if limit_file.exists() else 1024
            if requested_port >= first_unprivileged_port:
                return requested_port
            logger.warning(
                "port %s needs `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=%s` "
                "(persist it in /etc/sysctl.d/); using %s",
                requested_port, requested_port, LINUX_FALLBACK_ROUTER_PORT,
            )
            return LINUX_FALLBACK_ROUTER_PORT


@dataclass(frozen=True)
class Settings:
    min_port: int
    ignored_ports: frozenset
    ignored_process_prefixes: tuple
    router_port: int
    log_level: LogLevel
    trusted_peers: frozenset

    KNOWN_KEYS = ("min_port", "ignored_ports", "ignored_process_prefixes", "router_port", "log_level", "trusted_peers")

    @staticmethod
    def load():
        try:
            raw = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        except (OSError, ValueError) as error:
            raise SystemExit(f"{CONFIG_FILE}: {error}") from None
        if not isinstance(raw, dict):
            raise SystemExit(f"{CONFIG_FILE}: expected a JSON object")
        unknown = set(raw) - set(Settings.KNOWN_KEYS)
        if unknown:
            raise SystemExit(f"{CONFIG_FILE}: unknown keys {sorted(unknown)}; valid keys are {list(Settings.KNOWN_KEYS)}")
        for field in ("min_port", "router_port"):
            port = raw.get(field, DEFAULT_MIN_PORT if field == "min_port" else DEFAULT_ROUTER_PORT)
            if type(port) is not int or not 1 <= port <= 65535:
                raise SystemExit(f"{CONFIG_FILE}: {field} must be an integer from 1 to 65535")
        ignored_ports = raw.get("ignored_ports", [])
        if not isinstance(ignored_ports, list) or any(type(port) is not int or not 1 <= port <= 65535 for port in ignored_ports):
            raise SystemExit(f"{CONFIG_FILE}: ignored_ports must be a list of integers from 1 to 65535")
        prefixes = raw.get("ignored_process_prefixes", DEFAULT_IGNORED_PROCESS_PREFIXES)
        if not isinstance(prefixes, list) or any(not isinstance(prefix, str) or not prefix for prefix in prefixes):
            raise SystemExit(f"{CONFIG_FILE}: ignored_process_prefixes must be a list of nonempty strings")
        peers = raw.get("trusted_peers", [])
        if not isinstance(peers, list) or any(not isinstance(peer, str) for peer in peers):
            raise SystemExit(f"{CONFIG_FILE}: trusted_peers must be a list of IP address strings")
        try:
            for peer in peers:
                ipaddress.ip_address(peer)
        except ValueError:
            raise SystemExit(f"{CONFIG_FILE}: trusted_peers must contain valid IP addresses") from None
        log_level_name = raw.get("log_level", LogLevel.INFO.value)
        try:
            log_level = LogLevel(log_level_name)
        except (ValueError, TypeError):
            choices = [level.value for level in LogLevel]
            raise SystemExit(f"{CONFIG_FILE}: log_level {log_level_name!r}; valid values are {choices}") from None
        return Settings(
            min_port=raw.get("min_port", DEFAULT_MIN_PORT),
            ignored_ports=frozenset(ignored_ports),
            ignored_process_prefixes=tuple(prefixes),
            router_port=raw.get("router_port", DEFAULT_ROUTER_PORT),
            log_level=log_level,
            trusted_peers=frozenset(peers),
        )

    def is_ignored(self, listener):
        return listener.port in self.ignored_ports or listener.process_name.startswith(self.ignored_process_prefixes)


def configure_logging(level):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"))
    logger.addHandler(handler)
    logger.setLevel(level.value.upper())
    # Every thread's crash lands in the capped log, not in the uncapped stderr.
    threading.excepthook = lambda failure: logger.error(
        "thread %s crashed", failure.thread.name if failure.thread else "?",
        exc_info=(failure.exc_type, failure.exc_value, failure.exc_traceback),
    )
    sys.excepthook = lambda *failure: logger.critical("daemon crashed", exc_info=failure)


class Notifier:
    """herdr notifications, at most one per machine and topic every NOTIFICATION_GAP_S,
    so a flapping listener cannot flood the screen."""

    def __init__(self):
        self.last_sent_at = {}

    def send(self, machine_label, title, body, topic="ports"):
        now = time.monotonic()
        throttle_key = (machine_label, topic)
        if now - self.last_sent_at.get(throttle_key, -NOTIFICATION_GAP_S) < NOTIFICATION_GAP_S:
            logger.info("notification held back: %s: %s", title, body)
            return
        self.last_sent_at[throttle_key] = now
        logger.info("notification: %s: %s", title, body)
        try:
            subprocess.run(
                [HERDR, "notification", "show", printable(title), "--body", printable(body, REMOTE_URL_MAX_CHARS), "--sound", "none"],
                capture_output=True, timeout=COMMAND_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.warning("notification failed: %s", error)


class FileLinks:
    """Capability URLs for remote files: http://files.<machine>.localhost/<signature>/<folder>/<file>.

    The folder is one percent-encoded path segment, so relative links in the page
    (CSS, images, other pages) resolve inside it. The signature is an HMAC of the
    machine and folder, so a web page cannot guess a URL that reads another
    remote file, and links keep working across daemon restarts."""

    def __init__(self, router_port):
        self.router_port = router_port
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if not FILE_LINK_KEY_FILE.exists():
            FILE_LINK_KEY_FILE.touch(mode=0o600)
            FILE_LINK_KEY_FILE.write_bytes(secrets.token_bytes(32))
        self.key = FILE_LINK_KEY_FILE.read_bytes()

    def signature(self, machine_hostname, folder):
        return hmac.new(self.key, f"{machine_hostname}\0{folder}".encode(), hashlib.sha256).hexdigest()[:24]

    def url(self, machine_hostname, remote_path):
        folder, file_name = posixpath.split(posixpath.normpath(remote_path))
        return (
            f"http://files.{machine_hostname}.localhost{router_suffix(self.router_port)}/"
            f"{self.signature(machine_hostname, folder)}/{urllib.parse.quote(folder, safe='')}/{urllib.parse.quote(file_name)}"
        )

    def resolve(self, machine_hostname, request_path):
        """(folder, remote file) a request path names, or None for a forged link or one that leaves its folder."""
        segments = request_path.split("?", 1)[0].split("#", 1)[0].lstrip("/").split("/", 2)
        if len(segments) != 3:
            return None
        signature, quoted_folder, quoted_rest = segments
        if not FILE_LINK_SIGNATURE.match(signature):
            return None
        folder = urllib.parse.unquote(quoted_folder)
        rest = urllib.parse.unquote(quoted_rest) or "index.html"
        if "\0" in folder or "\0" in rest or not hmac.compare_digest(signature, self.signature(machine_hostname, folder)):
            return None
        if rest.endswith("/"):
            rest += "index.html"
        remote_path = posixpath.normpath(posixpath.join(folder, rest))
        return (folder, remote_path) if remote_path.startswith(folder.rstrip("/") + "/") else None


def router_suffix(router_port):
    return "" if router_port == 80 else f":{router_port}"


def route_url(machine_hostname, port, router_port):
    return f"http://{port}.{machine_hostname}.localhost{router_suffix(router_port)}"


def machine_hostname(label):
    """The DNS label a machine gets under .localhost: its herdr label, so `herdr machine
    rename` is the one place a machine's URL name is chosen."""
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "machine"


class ConnectionState(enum.Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RETRYING = "retrying"


class RemoteMessageKind(enum.Enum):
    PORTS = "ports"
    OPEN = "open"
    FATAL = "fatal"


class ProtocolError(ValueError):
    """watch.sh sent a line bifrost does not accept. Remote input is untrusted."""


class Family(enum.Enum):
    IPV4 = "4"
    IPV6 = "6"


@dataclass(frozen=True)
class Listener:
    port: int
    family: Family
    process_name: str

    @property
    def remote_host(self):
        match self.family:
            case Family.IPV4:
                return "127.0.0.1"
            case Family.IPV6:
                return "[::1]"

    @staticmethod
    def parse(token, min_port):
        """Parse watch.sh's PORT/FAMILY/PROCESS_NAME token."""
        match_token = LISTENER_TOKEN.match(token)
        if not match_token:
            raise ProtocolError(f"listener token {printable(token)!r} is not PORT/4|6/NAME")
        port = int(match_token.group(1))
        if not min_port <= port <= 65535:
            raise ProtocolError(f"listener port {port} is outside {min_port}-65535")
        return Listener(port, Family(match_token.group(2)), match_token.group(3))


class OpenKind(enum.Enum):
    REMOTE_FILE = "remote file"
    REMOTE_PAGE = "remote page"
    EXTERNAL_PAGE = "external page"


class Machine:
    """One saved machine: an SSH master that streams listeners, carries every
    forward, and passes on what the machine asks to open."""

    def __init__(self, profile, settings, file_links, notifier, pending_opens, on_change):
        self.profile_id = profile["id"]
        self.label = profile["label"]
        self.ssh_target = profile["target"]
        self.hostname = machine_hostname(self.label)
        self.settings = settings
        self.file_links = file_links
        self.notifier = notifier
        self.pending_opens = pending_opens
        self.on_change = on_change
        self.state = ConnectionState.CONNECTING
        self.error = ""
        self.is_selected = bool(profile.get("selected"))
        self.is_stopping = False
        self.routed = {}  # port -> Listener with a live socket forward; written by this machine's thread only
        self.failed_listeners = {}  # port -> Listener whose forward failed, retried every FAILED_FORWARD_RETRY_S
        self.auto_opened_at = collections.deque(maxlen=AUTO_OPENS_PER_MINUTE)
        self.last_retry_at = 0.0
        self.ssh = None
        self.ssh_lock = threading.Lock()
        self.stderr_tail = collections.deque(maxlen=20)
        # Per instance, so a re-enabled machine's sockets never collide with the
        # ones its previous thread is still cleaning up.
        instance = secrets.token_hex(2)
        digest = hashlib.sha256(f"{self.profile_id}\0{self.ssh_target}".encode()).hexdigest()[:10]
        self.socket_prefix = f"bifrost-{digest}-{instance}"
        self.control_socket = SOCKET_DIR / self.socket_prefix
        host_tag = re.sub(r"[^A-Za-z0-9-]+", "-", socket.gethostname())[:20]
        self.watcher_id = f"{host_tag}-{instance}"
        self.thread = threading.Thread(target=self.run, name=f"machine {self.label}", daemon=True)

    def route_socket(self, port):
        return SOCKET_DIR / f"{self.socket_prefix}-{port}"

    def start(self):
        self.thread.start()

    def stop(self):
        with self.ssh_lock:
            self.is_stopping = True
            if self.ssh:
                self.ssh.terminate()  # forwards close with the master connection

    def run(self):
        retry_s = 1
        while not self.is_stopping:
            connected_at = time.monotonic()
            try:
                self.error = self.stream_listeners() or "connection closed"
            except Exception as error:  # a bug in one connection must not end the machine for good
                logger.exception("%s: connection failed", self.label)
                self.error = f"internal error: {error}"
            finally:
                self.close_connection()
            if self.is_stopping:
                break
            retry_s = 1 if time.monotonic() - connected_at > HEALTHY_CONNECTION_S else min(retry_s * 2, RETRY_MAX_S)
            self.state = ConnectionState.RETRYING
            self.on_change()
            logger.warning("%s: %s; retrying in %ss", self.label, printable(self.error, REMOTE_URL_MAX_CHARS), retry_s)
            time.sleep(retry_s)

    def ssh_command(self, *remote_command):
        return master_ssh_command(self.control_socket, *remote_command)

    def stream_listeners(self):
        """Run watch.sh until the connection ends. Returns the reason it ended."""
        SOCKET_DIR.mkdir(mode=0o700, exist_ok=True)
        self.control_socket.unlink(missing_ok=True)  # a stale socket disables multiplexing
        self.state = ConnectionState.CONNECTING
        self.on_change()
        watch_script = (PLUGIN_ROOT / "watch.sh").read_text()
        remote_command = f"sh -c {shlex.quote(watch_script)} bifrost-watch {self.settings.min_port} {self.watcher_id}"
        with self.ssh_lock:
            if self.is_stopping:
                return "stopping"
            # The overrides keep the user's ssh config from breaking a scripted
            # session: RemoteCommand/RequestTTY would replace the watcher, and
            # configured forwards would clash with an interactive session.
            self.ssh = subprocess.Popen(
                [
                    "ssh", "-M", "-S", str(self.control_socket),
                    "-o", "BatchMode=yes", "-o", "ControlPersist=no",
                    "-o", "RemoteCommand=none", "-o", "RequestTTY=no",
                    "-o", "ClearAllForwardings=yes", "-o", "ExitOnForwardFailure=no",
                    "-o", "StreamLocalBindUnlink=yes", "-o", "StreamLocalBindMask=0177",
                    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
                    "--", self.ssh_target, remote_command,
                ],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        # ssh logs a line per refused channel; unread, its stderr pipe fills and
        # ssh blocks, freezing every forward on this machine.
        threading.Thread(target=self.drain_stderr, args=(self.ssh,), name=f"ssh stderr {self.label}", daemon=True).start()
        threading.Thread(target=self.send_ticks, args=(self.ssh,), name=f"ticks {self.label}", daemon=True).start()
        for line in self.ssh.stdout:
            line = line.rstrip("\n")
            if not line:
                self.retry_failed_forwards()
                continue
            logger.debug("%s: watch.sh: %s", self.label, printable(line, REMOTE_URL_MAX_CHARS))
            try:
                fatal_reason = self.handle_remote_line(line)
            except ProtocolError as error:
                logger.warning("%s: ignored a line from watch.sh: %s", self.label, error)
                continue
            if fatal_reason:
                self.is_stopping = True
                return fatal_reason
        self.ssh.wait(timeout=COMMAND_TIMEOUT_S)
        return self.stderr_tail[-1] if self.stderr_tail else f"ssh exited {self.ssh.returncode}"

    def drain_stderr(self, ssh):
        for line in ssh.stderr:
            line = printable(line.rstrip("\n"), REMOTE_URL_MAX_CHARS)
            self.stderr_tail.append(line)
            logger.debug("%s: ssh: %s", self.label, line)

    def send_ticks(self, ssh):
        # watch.sh scans once per tick and ends when ticks stop, so a dead
        # connection never leaves a watcher on the machine claiming opens.
        try:
            while ssh.poll() is None:
                ssh.stdin.write(f"tick {1 if self.is_selected else 0}\n")
                ssh.stdin.flush()
                time.sleep(TICK_S)
        except (BrokenPipeError, OSError, ValueError):
            pass

    def handle_remote_line(self, line):
        """Act on one watch.sh line. Returns a reason when the machine cannot be watched."""
        kind_word, _, rest = line.partition(" ")
        try:
            kind = RemoteMessageKind(kind_word)
        except ValueError:
            raise ProtocolError(f"message kind {printable(kind_word)!r}; valid kinds are {[k.value for k in RemoteMessageKind]}") from None
        match kind:
            case RemoteMessageKind.PORTS:
                listeners = [Listener.parse(token, self.settings.min_port) for token in rest.split()]
                if self.state is not ConnectionState.CONNECTED:
                    self.state = ConnectionState.CONNECTED
                    self.error = ""
                    logger.info("%s: connected", self.label)
                    threading.Thread(target=self.refresh_remote_opener, name=f"opener {self.label}", daemon=True).start()
                self.reconcile(listeners)
            case RemoteMessageKind.OPEN:
                if not OPEN_REQUEST.match(rest) or len(rest) > 4096:
                    raise ProtocolError(f"open request {printable(rest, REMOTE_URL_MAX_CHARS)!r} is not an http(s) or file URL or an absolute path")
                # Off this thread: opening may wait for the URL's port to be forwarded.
                threading.Thread(target=self.open_request, args=(rest,), name=f"open {self.label}", daemon=True).start()
            case RemoteMessageKind.FATAL:
                return printable(rest, REMOTE_URL_MAX_CHARS)
        return None

    def refresh_remote_opener(self):
        """Keep the machine's opener at this plugin version, if `bifrost setup` installed one."""
        try:
            completed = install_remote_opener(self.control_socket, should_create=False)
        except subprocess.TimeoutExpired:
            logger.warning("%s: refreshing the remote opener timed out", self.label)
            return
        if completed.returncode != 0:
            logger.warning("%s: refreshing the remote opener failed: %s", self.label, printable(completed.stderr.strip(), REMOTE_URL_MAX_CHARS))

    def reconcile(self, listeners):
        wanted = {listener.port: listener for listener in listeners if not self.settings.is_ignored(listener)}
        for port, listener in list(self.routed.items()):
            # A listener that moved to the other family needs a new target.
            if wanted.get(port) != listener:
                self.mux("cancel", self.route_spec(listener))
                del self.routed[port]
                self.route_socket(port).unlink(missing_ok=True)
                logger.info("%s: :%s closed", self.label, port)
        self.failed_listeners = {port: listener for port, listener in self.failed_listeners.items() if wanted.get(port) == listener}
        new_listeners = [listener for port, listener in wanted.items() if port not in self.routed and port not in self.failed_listeners]
        for listener in new_listeners:
            self.add_route(listener)
        self.on_change()
        if new_listeners:
            ports = ", ".join(f":{listener.port} {listener.process_name}" for listener in new_listeners)
            self.notifier.send(self.label, f"{self.label}: {len(new_listeners)} new port(s)", ports)

    def add_route(self, listener):
        if self.mux("forward", self.route_spec(listener)):
            self.routed[listener.port] = listener
            self.failed_listeners.pop(listener.port, None)
            logger.info("%s: routed :%s (%s) at %s", self.label, listener.port, listener.process_name,
                        route_url(self.hostname, listener.port, self.settings.router_port))
        else:
            self.failed_listeners[listener.port] = listener
        self.last_retry_at = time.monotonic()

    def retry_failed_forwards(self):
        if self.failed_listeners and time.monotonic() - self.last_retry_at >= FAILED_FORWARD_RETRY_S:
            for listener in list(self.failed_listeners.values()):
                self.add_route(listener)
            self.on_change()

    def route_spec(self, listener):
        return f"{self.route_socket(listener.port)}:{listener.remote_host}:{listener.port}"

    def mux(self, operation, spec):
        try:
            completed = subprocess.run(
                self.ssh_command("-O", operation, "-L", spec, "_"), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            logger.warning("%s: ssh -O %s %s timed out", self.label, operation, spec)
            return False
        if completed.returncode != 0:
            logger.warning("%s: ssh -O %s %s failed: %s", self.label, operation, spec, printable(completed.stderr.strip(), 200))
        return completed.returncode == 0

    def close_connection(self):
        with self.ssh_lock:
            ssh = self.ssh
            self.ssh = None
        if ssh:
            if ssh.poll() is None:
                ssh.terminate()
            try:
                ssh.wait(timeout=COMMAND_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                ssh.kill()
                ssh.wait()
        for port in list(self.routed):
            self.route_socket(port).unlink(missing_ok=True)
        self.routed.clear()
        self.failed_listeners.clear()
        self.control_socket.unlink(missing_ok=True)

    def open_request(self, request):
        try:
            self.open_here(request)
        except Exception:  # one bad request must not take the machine down
            logger.exception("%s: open request failed", self.label)

    def open_here(self, request):
        """Decide how a page the machine asked for opens here.

        The machine's own pages and files open at once (rate limited). Any other
        page waits for a yes in the confirm popup, because a prompt-injected agent
        could otherwise open phishing pages here without a click."""
        if request.startswith(("/", "file://")):
            remote_path = urllib.parse.unquote(urllib.parse.urlsplit(request).path) if request.startswith("file://") else request
            remote_path = posixpath.normpath(remote_path)
            if posixpath.dirname(remote_path) == "/":
                self.notifier.send(self.label, f"{self.label}: file not opened", f"{remote_path}: files directly under / are not served", topic="open")
                return
            self.open_by_kind(OpenKind.REMOTE_FILE, self.file_links.url(self.hostname, remote_path), request)
            return
        parts = urllib.parse.urlsplit(request)
        if parts.hostname not in REMOTE_LOOPBACK_HOSTS:
            self.open_by_kind(OpenKind.EXTERNAL_PAGE, request, request)
            return
        port = parts.port
        if not port:
            logger.info("%s: %s names no port; not opened", self.label, printable(request, REMOTE_URL_MAX_CHARS))
            return
        # The page usually opens the moment its server starts, before watch.sh
        # has reported the new listener.
        deadline = time.monotonic() + OPEN_FORWARD_WAIT_S
        while port not in self.routed and time.monotonic() < deadline:
            time.sleep(0.2)
        if port not in self.routed:
            self.notifier.send(self.label, f"{self.label}: page not opened", f"nothing listens on :{port} for {request}", topic="open")
            return
        if parts.scheme == "https":
            # The router reads a plaintext Host header, so it cannot route TLS.
            self.notifier.send(self.label, f"{self.label}: https page not opened",
                               f"{request}: run `bifrost forward {self.hostname}:{port}`, then open https://localhost:{port}", topic="open")
            return
        route_netloc = route_url(self.hostname, port, self.settings.router_port).removeprefix("http://")
        self.open_by_kind(OpenKind.REMOTE_PAGE, urllib.parse.urlunsplit(parts._replace(netloc=route_netloc)), request)

    def open_by_kind(self, kind, local_url, request):
        match kind:
            case OpenKind.REMOTE_FILE | OpenKind.REMOTE_PAGE:
                now = time.monotonic()
                if len(self.auto_opened_at) == AUTO_OPENS_PER_MINUTE and now - self.auto_opened_at[0] < 60:
                    logger.warning("%s: more than %s opens a minute; asking first", self.label, AUTO_OPENS_PER_MINUTE)
                    self.pending_opens.add(self.label, local_url, request)
                    return
                self.auto_opened_at.append(now)
                open_locally(local_url)
            case OpenKind.EXTERNAL_PAGE:
                self.pending_opens.add(self.label, local_url, request)

    async def read_file(self, folder, remote_path):
        """Start streaming one remote file over the master. Returns (size, process, error)."""
        fetch = await asyncio.create_subprocess_exec(
            *self.ssh_command("_", f"sh -c {shlex.quote(REMOTE_FILE_SCRIPT)} bifrost-file {shlex.quote(folder)} {shlex.quote(remote_path)}"),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            first_line = await asyncio.wait_for(fetch.stdout.readline(), FILE_READ_TIMEOUT_S)
        except asyncio.TimeoutError:
            await stop_process(fetch)
            return None, None, "504 Gateway Timeout"
        status_word, _, size_text = first_line.decode(errors="replace").strip().partition(" ")
        if status_word == "ok" and size_text.isdigit():
            return int(size_text), fetch, None
        exit_code = await fetch.wait()
        if exit_code == REMOTE_FILE_OUTSIDE_EXIT:
            return None, None, "403 Forbidden"
        if exit_code == REMOTE_FILE_MISSING_EXIT:
            return None, None, "404 Not Found"
        return None, None, "502 Bad Gateway"

    def snapshot(self):
        return {
            "label": self.label,
            "hostname": self.hostname,
            "state": self.state.value,
            "is_selected": self.is_selected,
            "error": printable(self.error, REMOTE_URL_MAX_CHARS),
            "control_socket": str(self.control_socket),
            "ports": [
                {"port": port, "process_name": listener.process_name,
                 "route_url": route_url(self.hostname, port, self.settings.router_port)}
                for port, listener in sorted(dict(self.routed).items())
            ],
            "failed_ports": sorted(dict(self.failed_listeners)),
        }


def master_ssh_command(control_socket, *remote_command):
    """argv for a command over a machine's existing master. If the master is gone,
    the ProxyCommand stops ssh from dialling a real host named `_`."""
    return [
        "ssh", "-F", "/dev/null", "-o", "ProxyCommand=/usr/bin/false", "-o", "BatchMode=yes",
        "-o", "ControlMaster=no", "-S", str(control_socket), *remote_command,
    ]


def install_remote_opener(control_socket, should_create):
    mode = "create" if should_create else "refresh"
    return subprocess.run(
        master_ssh_command(control_socket, "_", f"sh -c {shlex.quote(REMOTE_OPENER_INSTALL_SCRIPT)} bifrost-install {mode}"),
        input=(PLUGIN_ROOT / "remote-open.sh").read_text(), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
    )


async def stop_process(process):
    if process.returncode is None:
        process.kill()
    await process.wait()


class PendingOpens:
    """Pages a machine asked to open that need a yes first. A JSON file under a
    lock, because the daemon adds entries and the confirm popup removes them."""

    def __init__(self, notifier):
        self.notifier = notifier
        self.last_popup_at = 0

    @staticmethod
    def edit(change):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(PENDING_OPENS_FILE, "a+") as pending_file:
            fcntl.flock(pending_file, fcntl.LOCK_EX)
            pending_file.seek(0)
            text = pending_file.read()
            entries = json.loads(text) if text.strip() else []
            entries = [e for e in entries if time.time() - e["queued_at"] < PENDING_OPEN_MAX_AGE_S]
            answer = change(entries)
            pending_file.seek(0)
            pending_file.truncate()
            pending_file.write(json.dumps(entries))
            return answer

    def add(self, machine_label, local_url, request):
        PendingOpens.edit(lambda entries: entries.append(
            {"machine": machine_label, "url": local_url, "request": request, "queued_at": time.time()}))
        logger.info("%s: asking before opening %s", machine_label, printable(local_url, REMOTE_URL_MAX_CHARS))
        self.notifier.send(machine_label, f"{machine_label} wants to open a page", printable(local_url, REMOTE_URL_MAX_CHARS), topic="confirm")
        self.show_popup()

    def show_popup(self):
        if not PendingOpens.edit(lambda entries: bool(entries)) or confirm_popup_is_open():
            return
        self.last_popup_at = time.monotonic()
        try:
            opened = subprocess.run(
                [HERDR, "plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "confirm"],
                capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
            )
            if opened.returncode != 0:
                logger.info("confirm popup not shown yet: %s", opened.stderr.strip() or opened.stdout.strip())
        except (OSError, subprocess.TimeoutExpired) as error:
            logger.warning("confirm popup failed: %s", error)

    def retry_popup(self):
        if time.monotonic() - self.last_popup_at >= CONFIRM_POPUP_RETRY_S:
            self.show_popup()


def confirm_popup_is_open():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIRM_LOCK_FILE, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


class HttpMethod(enum.Enum):
    GET = "GET"
    HEAD = "HEAD"


class HostRouter:
    """Serves http://<port>.<machine>.localhost by reading the Host header, then
    copying raw bytes to that port's socket forward, so WebSockets and HMR pass.
    Also serves files.<machine>.localhost and the index at localhost."""

    def __init__(self, settings, file_links, daemon):
        self.settings = settings
        self.file_links = file_links
        self.daemon = daemon
        self.thread = threading.Thread(target=lambda: asyncio.run(self.serve()), name="router", daemon=True)

    def start(self):
        self.thread.start()

    def listen_addresses(self):
        match PLATFORM:
            case Platform.MACOS:
                # macOS lets a normal user bind a port below 1024 only on the
                # wildcard address; handle() drops non-loopback peers unread.
                return ("0.0.0.0", "::")
            case Platform.WSL | Platform.LINUX:
                return ("127.0.0.1", "::1")

    async def serve(self):
        servers = []
        for address in self.listen_addresses():
            try:
                servers.append(await asyncio.start_server(self.handle, address, self.settings.router_port, limit=REQUEST_HEAD_MAX_BYTES))
            except OSError as error:
                logger.warning("router: cannot listen on [%s]:%s: %s", address, self.settings.router_port, error)
        if not servers:
            self.daemon.notifier.send("router", "bifrost router is off", f"port {self.settings.router_port} is taken; set router_port in {CONFIG_FILE}")
            return
        logger.info("router: listening on port %s", self.settings.router_port)
        await asyncio.gather(*(server.serve_forever() for server in servers))

    async def handle(self, client_reader, client_writer):
        peer_ip = (client_writer.get_extra_info("peername") or ("?",))[0]
        if peer_ip not in LOOPBACK_PEERS and peer_ip not in self.settings.trusted_peers:
            logger.info("router: dropped a connection from %s (not loopback; add it to trusted_peers to allow)", peer_ip)
            client_writer.close()
            return
        try:
            await self.answer(client_reader, client_writer)
        except Exception:  # one malformed request must not stop the router
            logger.exception("router: request failed")
            try:
                await respond(client_writer, "500 Internal Server Error", "bifrost hit an error; see `bifrost logs`.", "text/plain; charset=utf-8")
            except (ConnectionError, OSError):
                pass
        finally:
            client_writer.close()

    async def answer(self, client_reader, client_writer):
        try:
            head = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), REQUEST_HEAD_TIMEOUT_S)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        host = re.sub(r":\d+$", "", request_host(head))
        files_route = FILES_HOST.match(host)
        port_route = ROUTE_HOST.match(host)
        if files_route:
            status_line = await self.serve_file(client_writer, files_route.group(1), request_line)
        elif port_route:
            status_line = await self.proxy(client_reader, client_writer, port_route.group(2), int(port_route.group(1)), head)
        elif host in INDEX_HOSTS:
            status_line = "200 OK"
            await respond(client_writer, status_line, self.daemon.index_page())
        elif host.endswith(".localhost"):
            status_line = "404 Not Found"
            index_url = f"http://localhost{router_suffix(self.settings.router_port)}/"
            await respond(client_writer, status_line, f"No bifrost route for {html.escape(host)}. See {index_url}", "text/plain; charset=utf-8")
        else:
            # Any other Host means a DNS-rebinding page is asking; it gets nothing.
            status_line = "421 Misdirected Request"
            await respond(client_writer, status_line, "", "text/plain; charset=utf-8")
        logger.debug("router: %s %s -> %s", printable(host), printable(request_line, 200), status_line)

    async def proxy(self, client_reader, client_writer, hostname, port, head):
        socket_path = self.daemon.route_socket_for(hostname, port)
        if not socket_path:
            await respond(client_writer, "404 Not Found", f"Nothing on {hostname} listens on :{port}.", "text/plain; charset=utf-8")
            return "404 Not Found"
        try:
            upstream_reader, upstream_writer = await asyncio.open_unix_connection(str(socket_path))
        except OSError as error:
            await respond(client_writer, "502 Bad Gateway", f"The forward for :{port} failed: {error}", "text/plain; charset=utf-8")
            return "502 Bad Gateway"
        try:
            upstream_writer.write(head)
            await asyncio.gather(copy_bytes(client_reader, upstream_writer), copy_bytes(upstream_reader, client_writer))
        finally:
            upstream_writer.close()
        return "proxied"

    async def serve_file(self, client_writer, hostname, request_line):
        method_word, _, rest = request_line.partition(" ")
        request_path = rest.rsplit(" ", 1)[0]
        try:
            method = HttpMethod(method_word)
        except ValueError:
            await respond(client_writer, "405 Method Not Allowed", "Only GET and HEAD read remote files.", "text/plain; charset=utf-8")
            return "405 Method Not Allowed"
        resolved = self.file_links.resolve(hostname, request_path)
        if resolved is None:
            await respond(client_writer, "403 Forbidden", "This file link is not valid.", "text/plain; charset=utf-8")
            return "403 Forbidden"
        machine = self.daemon.machine_for(hostname)
        if machine is None:
            await respond(client_writer, "503 Service Unavailable", f"{hostname} is not connected.", "text/plain; charset=utf-8")
            return "503 Service Unavailable"
        folder, remote_path = resolved
        size, fetch, error_status = await machine.read_file(folder, remote_path)
        if error_status:
            await respond(client_writer, error_status, f"{remote_path}: {error_status}", "text/plain; charset=utf-8")
            return error_status
        content_type = mimetypes.guess_type(remote_path)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json", "image/svg+xml"):
            content_type += "; charset=utf-8"
        try:
            client_writer.write(response_head("200 OK", content_type, size))
            match method:
                case HttpMethod.GET:
                    while chunk := await asyncio.wait_for(fetch.stdout.read(COPY_CHUNK_BYTES), FILE_READ_TIMEOUT_S):
                        client_writer.write(chunk)
                        await client_writer.drain()
                case HttpMethod.HEAD:
                    pass
            await client_writer.drain()
        finally:
            await stop_process(fetch)
        logger.debug("router: served %s (%s bytes)", printable(remote_path, 200), size)
        return "200 OK"


def request_host(head):
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"host":
            return value.strip().decode("latin-1").lower()
    return ""


def response_head(status_line, content_type, content_length):
    # no-store: an agent rewrites the same file, and a reload should show the new one.
    return (
        f"HTTP/1.1 {status_line}\r\nContent-Type: {content_type}\r\nContent-Length: {content_length}\r\n"
        f"Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
        f"Content-Security-Policy: {FILE_PAGE_POLICY}\r\nConnection: close\r\n\r\n"
    ).encode()


async def respond(writer, status_line, body, content_type="text/html; charset=utf-8"):
    payload = body.encode()
    writer.write(response_head(status_line, content_type, len(payload)) + payload)
    await writer.drain()


async def copy_bytes(reader, writer):
    try:
        while chunk := await reader.read(COPY_CHUNK_BYTES):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()  # half-close, so the other direction can finish its reply
    except (ConnectionError, OSError):
        writer.close()


class Daemon:
    def __init__(self, settings):
        self.settings = settings
        self.machines = {}  # profile id -> Machine
        self.status_lock = threading.Lock()
        self.is_stopping = False
        self.notifier = Notifier()
        self.pending_opens = PendingOpens(self.notifier)
        self.file_links = FileLinks(settings.router_port)
        self.router = HostRouter(settings, self.file_links, self)
        socket_path = os.environ.get("HERDR_SOCKET_PATH") or (str(DEFAULT_HERDR_SOCKET) if DEFAULT_HERDR_SOCKET.exists() else "")
        self.herdr_socket = socket_path

    def machine_for(self, hostname):
        for machine in list(self.machines.values()):
            if machine.hostname == hostname and machine.state is ConnectionState.CONNECTED:
                return machine
        return None

    def route_socket_for(self, hostname, port):
        machine = self.machine_for(hostname)
        return machine.route_socket(port) if machine and port in dict(machine.routed) else None

    def index_page(self):
        sections = []
        for machine in list(self.machines.values()):
            snapshot = machine.snapshot()
            rows = "".join(
                f'<tr><td class="port">{port["port"]}</td><td>{html.escape(port["process_name"])}</td>'
                f'<td><a href="{port["route_url"]}">{port["route_url"]}</a></td></tr>'
                for port in snapshot["ports"]
            ) or '<tr><td colspan="3" class="muted">No ports listening.</td></tr>'
            error = f' <span class="muted">{html.escape(snapshot["error"])}</span>' if snapshot["error"] else ""
            sections.append(
                f'<section><h2>{html.escape(snapshot["label"])} <span class="state {snapshot["state"]}">{snapshot["state"]}</span>{error}</h2>'
                f"<table>{rows}</table></section>"
            )
        body = "".join(sections) or '<p class="muted">No enabled saved machines. Add one with <code>herdr machine add</code>.</p>'
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<meta http-equiv="refresh" content="5"><title>bifrost</title><style>'
            ":root{color-scheme:light dark;--muted:#6c7086;--line:#8884;--accent:#1e66f5}"
            "@media(prefers-color-scheme:dark){:root{--accent:#89b4fa}}"
            "body{font:15px/1.5 system-ui,sans-serif;max-width:52rem;margin:3rem auto;padding:0 1.5rem}"
            "h1{font-size:1.4rem;margin:0}h2{font-size:1rem;margin:2rem 0 .5rem}"
            "header p,.muted{color:var(--muted)}a{color:var(--accent)}"
            "table{border-collapse:collapse;width:100%}td{padding:.4rem .8rem .4rem 0;border-top:1px solid var(--line)}"
            "td a,.port,code{font-family:ui-monospace,monospace}.port{width:4rem}"
            ".state{font-size:.75rem;font-weight:500;padding:.1rem .5rem;border-radius:1rem;background:#8882}"
            ".connected{color:#40a02b}.retrying{color:#df8e1d}.connecting{color:var(--muted)}"
            f'</style><header><h1>bifrost</h1><p>Ports on your herdr machines. <a href="{REPOSITORY_URL}">Docs</a></p></header>'
            f"{body}</html>"
        )

    def write_status(self):
        with self.status_lock:
            snapshot = {
                "pid": os.getpid(),
                "router_port": self.settings.router_port,
                "platform": PLATFORM.value,
                "machines": [m.snapshot() for m in list(self.machines.values())],
            }
            partial = STATUS_FILE.with_suffix(".partial")
            partial.write_text(json.dumps(snapshot, indent=2))
            partial.replace(STATUS_FILE)

    def enabled_profiles(self):
        listed = subprocess.run([HERDR, "machine", "list", "--json"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
        if listed.returncode != 0:
            logger.warning("herdr machine list failed: %s", listed.stderr.strip())
            return None
        return {p["id"]: p for p in json.loads(listed.stdout) if p["enabled"]}

    def sync_machines(self):
        profiles = self.enabled_profiles()
        if profiles is None:
            return
        for profile_id, machine in list(self.machines.items()):
            profile = profiles.get(profile_id)
            # A rename changes the URL name and a new target the connection, so
            # either one rebuilds the machine.
            if profile is None or (profile["label"], profile["target"]) != (machine.label, machine.ssh_target):
                logger.info("%s: removed, disabled, renamed or retargeted", machine.label)
                self.machines.pop(profile_id).stop()
        hostnames_in_use = {m.hostname: m.label for m in self.machines.values()}
        for profile_id, profile in profiles.items():
            machine = self.machines.get(profile_id)
            if machine:
                machine.is_selected = bool(profile.get("selected"))
                continue
            hostname = machine_hostname(profile["label"])
            if hostname in hostnames_in_use:
                logger.warning("%s: hostname %s is taken by %s; rename one with `herdr machine rename`",
                               profile["label"], hostname, hostnames_in_use[hostname])
                continue
            machine = Machine(profile, self.settings, self.file_links, self.notifier, self.pending_opens, self.write_status)
            hostnames_in_use[hostname] = machine.label
            self.machines[profile_id] = machine
            machine.start()
        self.write_status()

    def herdr_server_is_up(self):
        if not self.herdr_socket:
            return True  # no herdr socket to follow; run until stopped
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(self.herdr_socket)
            return True
        except OSError:
            return False
        finally:
            probe.close()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "is_stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "is_stopping", True))
        logger.info("daemon %s started on %s, router port %s, following %s",
                    os.getpid(), PLATFORM.value, self.settings.router_port, self.herdr_socket or "no herdr socket")
        self.router.start()
        missed_checks = 0
        try:
            while not self.is_stopping:
                # herdr does not supervise plugin processes, so the daemon leaves
                # once the server it follows has been gone for a few checks.
                missed_checks = 0 if self.herdr_server_is_up() else missed_checks + 1
                if missed_checks >= HERDR_MISSED_CHECKS_MAX:
                    logger.info("herdr server gone")
                    break
                try:
                    self.sync_machines()
                    self.pending_opens.retry_popup()
                except Exception:  # keep the daemon alive through one bad pass
                    logger.exception("daemon loop pass failed")
                for _ in range(MACHINE_POLL_S * 10):
                    if self.is_stopping:
                        break
                    time.sleep(0.1)
        finally:
            for machine in list(self.machines.values()):
                machine.stop()
            for machine in list(self.machines.values()):
                machine.thread.join(timeout=COMMAND_TIMEOUT_S)
            STATUS_FILE.unlink(missing_ok=True)
            PID_FILE.unlink(missing_ok=True)
            logger.info("daemon stopped")


def running_daemon_pid():
    """The daemon's pid, checked against the live process so a stale or reused pid never counts."""
    pid, _ = running_daemon()
    return pid


def running_daemon():
    """(pid, command line) of the live daemon, or (None, "")."""
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None, ""
    command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return (pid, command) if "bifrost.py daemon" in command else (None, "")


def start():
    refresh_cli_shim()
    pid, command = running_daemon()
    if pid and str(Path(__file__).resolve()) in command:
        return
    # `herdr plugin update` installs into a new folder, and the hooks then run
    # this copy while the old daemon keeps the old code.
    if pid and not stop():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # The daemon's own stderr only catches what logging cannot (a fatal Python
    # error); cleared on each start, so it never grows.
    with open(CRASH_LOG_FILE, "w") as crash_log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "daemon"],
            stdin=subprocess.DEVNULL, stdout=crash_log, stderr=crash_log, start_new_session=True,
        )


def stop():
    pid = running_daemon_pid()
    if not pid:
        return True
    os.kill(pid, signal.SIGTERM)
    for _ in range(int(COMMAND_TIMEOUT_S * 10) + 50):
        if running_daemon_pid() is None:
            return True
        time.sleep(0.1)
    print(f"bifrost: daemon {pid} did not stop; see `bifrost logs`", file=sys.stderr)
    return False


def daemon():
    settings = Settings.load()
    configure_logging(settings.log_level)
    faulthandler.enable()  # a hard crash's traceback goes to crash.log
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.debug("another daemon holds the lock; exiting")
        return
    partial = PID_FILE.with_suffix(".partial")
    partial.write_text(str(os.getpid()))
    partial.replace(PID_FILE)
    settings = Settings(**{**settings.__dict__, "router_port": effective_router_port(settings.router_port)})
    # A daemon killed hard leaves its forward sockets behind; holding the lock
    # means none of them belongs to a live daemon.
    for leftover in SOCKET_DIR.glob("bifrost-*"):
        leftover.unlink(missing_ok=True)
    Daemon(settings).run()


def read_status():
    if not running_daemon_pid() or not STATUS_FILE.exists():
        return None
    return json.loads(STATUS_FILE.read_text())


class Style:
    """ANSI styles for a terminal that wants colour (https://no-color.org)."""

    def __init__(self, is_enabled):
        self.is_enabled = is_enabled

    def paint(self, text, code):
        return f"\x1b[{code}m{text}\x1b[0m" if self.is_enabled else text

    def state(self, state_name):
        match ConnectionState(state_name):
            case ConnectionState.CONNECTED:
                return self.paint("● connected", "32")
            case ConnectionState.CONNECTING:
                return self.paint("○ connecting", "2")
            case ConnectionState.RETRYING:
                return self.paint("● retrying", "33")


def status(wait_for_key):
    style = Style(sys.stdout.isatty() and "NO_COLOR" not in os.environ)
    snapshot = read_status()
    if snapshot is None:
        print(style.paint("bifrost is not running.", "31") + " Start it with `bifrost start`.")
        crash_report = CRASH_LOG_FILE.read_text().strip() if CRASH_LOG_FILE.exists() else ""
        if crash_report:
            print(f"\nLast crash ({CRASH_LOG_FILE}):\n" + "\n".join(crash_report.splitlines()[-10:]))
    else:
        if not snapshot["machines"]:
            print("No enabled saved machines. Add one with `herdr machine add`.")
        for machine in snapshot["machines"]:
            selected = style.paint("  selected", "2") if machine["is_selected"] else ""
            error = style.paint(f"  {machine['error']}", "2") if machine["error"] else ""
            print(f"{style.paint(machine['label'], '1')}  {style.state(machine['state'])}{selected}{error}")
            for port in machine["ports"]:
                print(f"  {style.paint(port['route_url'], '36'):<{36 + len(style.paint('', '36'))}} {style.paint(port['process_name'], '2')}")
            for port in machine["failed_ports"]:
                print(f"  :{port:<35} {style.paint('forward failed, retrying; see `bifrost logs`', '33')}")
            if not machine["ports"] and not machine["failed_ports"]:
                print(style.paint("  no ports listening", "2"))
    if wait_for_key:
        try:
            input(style.paint("\nPress Enter to close.", "2"))
        except EOFError:
            pass  # no terminal to wait on, as when stdin is closed


def connected_machine(machine_name):
    """The status entry for a machine named by label or hostname, or exit with the valid names."""
    snapshot = read_status()
    if snapshot is None:
        raise SystemExit("bifrost is not running. Start it with `bifrost start`.")
    names = {}
    for machine in snapshot["machines"]:
        names[machine["label"]] = names[machine["hostname"]] = machine
    if machine_name not in names:
        raise SystemExit(f"unknown machine {machine_name!r}; valid machines are {sorted(names)}")
    machine = names[machine_name]
    if machine["state"] != ConnectionState.CONNECTED.value:
        raise SystemExit(f"{machine['label']} is {machine['state']}; see `bifrost status`")
    return machine


def split_target(target, example):
    machine_name, separator, rest = target.partition(":")
    if not separator or not rest:
        raise SystemExit(f"expected <machine>:<...>, e.g. {example}")
    return machine_name, rest


def open_remote_file(target):
    """Open a remote file here, through the same signed link a file the machine opens gets."""
    machine_name, remote_path = split_target(target, "afk:~/report.html")
    machine = connected_machine(machine_name)
    # `~` stays unquoted so the remote shell expands it.
    quoted_path = "~/" + shlex.quote(remote_path[2:]) if remote_path.startswith("~/") else shlex.quote(remote_path)
    resolved = subprocess.run(
        master_ssh_command(machine["control_socket"], "_", f"realpath -e -- {quoted_path}"),
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
    )
    if resolved.returncode != 0:
        raise SystemExit(f"{machine['label']}: {remote_path}: {resolved.stderr.strip() or 'not found'}")
    absolute_path = resolved.stdout.strip()
    if posixpath.dirname(absolute_path) == "/":
        raise SystemExit(f"{absolute_path}: files directly under / are not served")
    url = FileLinks(read_status()["router_port"]).url(machine["hostname"], absolute_path)
    print(url)
    open_locally(url)


def forward_same_port(target, should_cancel):
    """Forward a remote port to the same localhost port here, for OAuth callbacks
    that insist on localhost:<port>. It lasts until the connection drops."""
    machine_name, port_text = split_target(target, "afk:1455")
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise SystemExit(f"port {port_text!r} is not a number from 1 to 65535")
    machine = connected_machine(machine_name)
    port = int(port_text)
    spec = f"127.0.0.1:{port}:127.0.0.1:{port}"
    operation = "cancel" if should_cancel else "forward"
    completed = subprocess.run(
        master_ssh_command(machine["control_socket"], "-O", operation, "-L", spec, "_"),
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
    )
    if completed.returncode != 0:
        raise SystemExit(f"ssh -O {operation} failed: {completed.stderr.strip()}")
    if should_cancel:
        print(f"stopped localhost:{port}")
    else:
        print(f"localhost:{port} now reaches {machine['label']}:{port} until the connection drops; `bifrost forward --stop {target}` ends it")


def confirm():
    """The confirm popup: ask about each pending page, one key per page."""
    with open(CONFIRM_LOCK_FILE, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another popup is already asking
        while entry := PendingOpens.edit(lambda entries: entries.pop(0) if entries else None):
            print(f"\n{printable(entry['machine'])} wants to open:\n\n  {printable(entry['url'], REMOTE_URL_MAX_CHARS)}\n")
            print("Enter or y: open    n or Esc: skip    q: skip all")
            key = read_key()
            if key in ("\r", "\n", "y"):
                open_locally(entry["url"])
            elif key == "q":
                PendingOpens.edit(lambda entries: entries.clear())
                return


def read_key():
    if not sys.stdin.isatty():
        return "n"
    saved = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved)


def show_logs(line_count, should_follow):
    if not LOG_FILE.exists():
        raise SystemExit(f"no log yet at {LOG_FILE}")
    with open(LOG_FILE) as log_file:
        for line in collections.deque(log_file, maxlen=line_count):
            print(line, end="")
    if not should_follow:
        return
    log_file = open(LOG_FILE)
    log_file.seek(0, os.SEEK_END)
    inode = os.fstat(log_file.fileno()).st_ino
    try:
        while True:
            line = log_file.readline()
            if line:
                print(line, end="", flush=True)
                continue
            time.sleep(0.5)
            # Rotation renames the file; follow the new one.
            if LOG_FILE.exists() and LOG_FILE.stat().st_ino != inode:
                log_file.close()
                log_file = open(LOG_FILE)
                inode = os.fstat(log_file.fileno()).st_ino
    except KeyboardInterrupt:
        pass
    finally:
        log_file.close()


def install_cli(bin_dir):
    """Put a `bifrost` command in bin_dir that runs this plugin, wherever herdr installed it."""
    shim = bin_dir / "bifrost"
    if (shim.exists() or shim.is_symlink()) and not is_cli_shim(shim):
        raise SystemExit(f"{shim} exists and is not bifrost's shim; remove it or pass --bin-dir")
    write_cli_shim(shim)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CLI_SHIM_RECORD_FILE.write_text(str(shim))
    print(f"installed {shim}")
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"{bin_dir} is not on PATH; add it in your shell rc file")


def refresh_cli_shim():
    # Points the shim at this copy after `herdr plugin update` moved the plugin.
    if not CLI_SHIM_RECORD_FILE.exists():
        return
    shim = Path(CLI_SHIM_RECORD_FILE.read_text().strip())
    if is_cli_shim(shim) and str(PLUGIN_ROOT / "bifrost") not in shim.read_text():
        write_cli_shim(shim)


def is_cli_shim(path):
    try:
        return not path.is_symlink() and CLI_SHIM_MARKER in path.read_text()[:512]
    except (OSError, UnicodeDecodeError):
        return False


def write_cli_shim(shim):
    shim.parent.mkdir(parents=True, exist_ok=True)
    partial = shim.with_name(".bifrost.partial")
    partial.write_text(
        f"#!/bin/sh\n{CLI_SHIM_MARKER}, written by `bifrost install-cli`. herdr installs each plugin\n"
        f"# update in a new folder; `bifrost start` points this line at the current one.\n"
        f"exec sh {shlex.quote(str(PLUGIN_ROOT / 'bifrost'))} \"$@\"\n"
    )
    partial.chmod(0o755)
    partial.replace(shim)


def set_up_machine(machine_name, should_undo):
    """Install or remove the opener on a machine, so the URLs its agents open come here."""
    machine = connected_machine(machine_name)
    control_socket = machine["control_socket"]
    if not should_undo:
        installed = install_remote_opener(control_socket, should_create=True)
        if installed.returncode != 0:
            raise SystemExit(f"{machine['label']}: installing the opener failed: {installed.stderr.strip()}")
    operation = "remove" if should_undo else "add"
    shell_setup = subprocess.run(
        master_ssh_command(control_socket, "_", f"sh -c {shlex.quote(REMOTE_SHELL_SETUP_SCRIPT)} bifrost-setup {operation}"),
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
    )
    if shell_setup.returncode != 0:
        raise SystemExit(f"{machine['label']}: editing the shell rc file failed: {shell_setup.stderr.strip()}")
    outcome, _, rc_file = shell_setup.stdout.strip().partition(" ")
    label = machine["label"]
    if should_undo:
        if outcome == "symlink":
            print(f"{label}: {rc_file} is a symlink, so bifrost left it alone; remove the PATH and BROWSER lines yourself")
        elif outcome == "removed":
            print(f"{label}: removed the bifrost lines from {rc_file}")
        print(f"{label}: removed the opener from ~/.local/share/bifrost/bin")
        return
    print(f"{label}: installed the opener as ~/.local/share/bifrost/bin/xdg-open")
    if outcome == "symlink":
        print(f"{label}: {rc_file} is a symlink, so bifrost left it alone. Add these lines where you manage it:\n"
              f'  export PATH="$HOME/.local/share/bifrost/bin:$PATH"\n'
              f'  export BROWSER="$HOME/.local/share/bifrost/bin/xdg-open"')
        return
    print(f"{label}: {'PATH and BROWSER already set in' if outcome == 'present' else 'set PATH and BROWSER in'} {rc_file}")
    print(f"{label}: restart the shells (and agents) there; then `xdg-open https://example.com` on {label} asks here")


def open_clicked_link(url):
    """A localhost link clicked in herdr: open it on the machine herdr shows, if
    that machine listens on the port, else here."""
    if not OPEN_REQUEST.fullmatch(url):
        raise SystemExit("open-link expects an http://localhost:<port> URL")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or 80
    except ValueError:
        raise SystemExit("open-link expects a valid port from 1 to 65535") from None
    if parts.scheme != "http" or parts.hostname not in REMOTE_LOOPBACK_HOSTS or parts.username or parts.password:
        raise SystemExit("open-link expects an http://localhost:<port> URL")
    snapshot = read_status() or {"machines": []}
    for machine in snapshot["machines"]:
        if not machine["is_selected"] or parts.scheme != "http":
            continue
        port = parts.port or 80
        for routed_port in machine["ports"]:
            if routed_port["port"] == port:
                route_netloc = routed_port["route_url"].removeprefix("http://")
                url = urllib.parse.urlunsplit(parts._replace(netloc=route_netloc))
    open_locally(url)


def show():
    subprocess.run([HERDR, "plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "status"])


OVERVIEW = """\
bifrost brings herdr saved machines to this computer's browser.

  ports   Every port a machine listens on opens at
            http://<port>.<machine>.localhost
          `bifrost forward` adds a same-port localhost:<port> when a tool insists.
  URLs    A page the machine opens (xdg-open, $BROWSER) opens here: its own
          pages and files at once, any other page after a yes in a popup.
  files   A file opened on the machine, or with `bifrost open`, is served at
            http://files.<machine>.localhost/...

<machine> is the herdr machine label: `herdr machine rename <id> --label afk`.
The daemon starts with herdr. `bifrost setup <machine>` once per machine makes
the URLs its agents open come here.
"""

EPILOG = f"""\
examples:
  bifrost status                          list machines, ports and their URLs
  bifrost setup afk                       send the URLs afk opens to this browser
  bifrost open afk:~/project/report.html  open a remote file in this browser
  bifrost forward afk:1455                localhost:1455 here reaches afk:1455
  bifrost logs -f                         follow the daemon log

files:
  config  {CONFIG_FILE}
          keys: {", ".join(Settings.KNOWN_KEYS)}
  log     {LOG_FILE} (rotates at 1 MB, keeps 3)
  docs    {REPOSITORY_URL}
"""


def command_line():
    import argparse

    parser = argparse.ArgumentParser(prog="bifrost", description=OVERVIEW, epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"bifrost {plugin_version()}")
    commands = parser.add_subparsers(title="commands", dest="command", metavar="<command>")
    status_command = commands.add_parser("status", help="list machines, routed ports and their URLs")
    status_command.add_argument("--wait", action="store_true", help="wait for Enter before exiting (for the herdr popup)")
    open_command = commands.add_parser(
        "open", help="open a remote file in this computer's browser",
        description="Open a file from a machine in this computer's browser. Relative links in the page load too.",
        epilog="examples:\n  bifrost open afk:~/project/report.html\n  bifrost open afk:/srv/site/index.html",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    open_command.add_argument("target", metavar="<machine>:<path>", help="machine label, a colon, then a path on that machine; ~/ is its home")
    forward_command = commands.add_parser(
        "forward", help="forward a remote port to the same localhost port here",
        description="Make localhost:<port> here reach <port> on the machine, for OAuth callbacks and tools that insist on "
                    "localhost. It lasts until the connection drops.",
    )
    forward_command.add_argument("target", metavar="<machine>:<port>", help="machine label, a colon, then the port")
    forward_command.add_argument("--stop", action="store_true", help="remove the forward instead")
    setup_command = commands.add_parser(
        "setup", help="make the URLs a machine's agents open come to this browser",
        description="Install the bifrost opener on a machine as ~/.local/share/bifrost/bin/xdg-open, and set PATH and "
                    "BROWSER in the rc file of its login shell (~/.zshenv, ~/.bashrc or ~/.profile). Run once per "
                    "machine; the daemon keeps the opener up to date.",
        epilog="examples:\n  bifrost setup afk\n  bifrost setup --undo afk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup_command.add_argument("machine", metavar="<machine>", help="machine label")
    setup_command.add_argument("--undo", action="store_true", help="remove the opener and the rc file lines")
    open_link_command = commands.add_parser(
        "open-link", help="open a localhost link on the machine herdr shows (Ctrl+click runs this)",
    )
    open_link_command.add_argument("url", nargs="?", default=os.environ.get("HERDR_PLUGIN_CLICKED_URL"), help="defaults to $HERDR_PLUGIN_CLICKED_URL")
    install_cli_command = commands.add_parser(
        "install-cli", help="put a `bifrost` command on PATH",
        description="Write a small `bifrost` script that runs this plugin. It follows `herdr plugin update`.",
    )
    install_cli_command.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin", help="where to put it (default ~/.local/bin)")
    logs_command = commands.add_parser("logs", help="show the daemon log")
    logs_command.add_argument("-n", type=int, default=50, metavar="LINES", help="how many lines to show (default 50)")
    logs_command.add_argument("-f", "--follow", action="store_true", help="keep printing new lines")
    commands.add_parser("restart", help="stop the daemon and start it again, reconnecting every machine")
    commands.add_parser("start", help="start the daemon unless it runs (herdr does this at startup)")
    commands.add_parser("stop", help="stop the daemon; every route closes")
    commands.add_parser("show", help="open the status popup in herdr (the Bifrost: status action)")
    commands.add_parser("confirm", help="ask about pending pages (the popup herdr opens)")
    commands.add_parser("daemon", help="run the daemon in the foreground (start runs this in the background)")
    return parser


def main():
    parser = command_line()
    arguments = parser.parse_args()
    match arguments.command:
        case None:
            parser.print_help()
        case "start":
            start()
        case "daemon":
            daemon()
        case "stop":
            stop()
        case "restart":
            if stop():
                start()
        case "status":
            status(wait_for_key=arguments.wait)
        case "show":
            show()
        case "open":
            open_remote_file(arguments.target)
        case "forward":
            forward_same_port(arguments.target, should_cancel=arguments.stop)
        case "logs":
            show_logs(arguments.n, arguments.follow)
        case "setup":
            set_up_machine(arguments.machine, should_undo=arguments.undo)
        case "open-link":
            if not arguments.url:
                parser.error("open-link needs a URL")
            open_clicked_link(arguments.url)
        case "install-cli":
            install_cli(arguments.bin_dir.expanduser())
        case "confirm":
            confirm()


if __name__ == "__main__":
    main()
