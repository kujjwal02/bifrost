"""Unit tests for bifrost.py and its shell scripts. Stdlib only: python -m unittest."""

import atexit
import importlib.util
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = PLUGIN_ROOT / ".scratch"
SCRATCH_DIR.mkdir(exist_ok=True)
os.environ["TMPDIR"] = str(SCRATCH_DIR)
tempfile.tempdir = str(SCRATCH_DIR)
STATE_DIR = tempfile.mkdtemp(prefix="bifrost-test-state-")
os.environ["HERDR_PLUGIN_STATE_DIR"] = STATE_DIR
os.environ["HERDR_PLUGIN_CONFIG_DIR"] = tempfile.mkdtemp(prefix="bifrost-test-config-")

atexit.register(shutil.rmtree, STATE_DIR)
atexit.register(shutil.rmtree, os.environ["HERDR_PLUGIN_CONFIG_DIR"])

module_spec = importlib.util.spec_from_file_location("bifrost", PLUGIN_ROOT / "bifrost.py")
bifrost = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(bifrost)

IS_LINUX = sys.platform.startswith("linux")


class RecordedPendingOpens:
    def __init__(self):
        self.urls = []

    def add(self, label, url, request):
        self.urls.append(url)


def make_machine(pending_opens=None):
    settings = bifrost.Settings(1024, frozenset(), ("code-",), 80, bifrost.LogLevel.INFO, frozenset())
    return bifrost.Machine(
        {"id": "m1", "label": "AFK Agents", "target": "afk"}, settings, bifrost.FileLinks(80),
        bifrost.Notifier(), pending_opens or RecordedPendingOpens(), lambda: None,
    )


class ListenerTokenTest(unittest.TestCase):
    def test_parses_port_family_and_process(self):
        listener = bifrost.Listener.parse("5173/6/node", 1024)
        self.assertEqual((listener.port, listener.family, listener.process_name), (5173, bifrost.Family.IPV6, "node"))
        self.assertEqual(listener.remote_host, "[::1]")

    def test_rejects_bad_tokens(self):
        for token in ["80/4/nginx", "70000/4/x", "5000/9/x", "5000/4/a$(b)", "5000/4/", "5000/4/" + "a" * 33]:
            with self.subTest(token=token), self.assertRaises(bifrost.ProtocolError):
                bifrost.Listener.parse(token, 1024)


class RemoteLineTest(unittest.TestCase):
    def test_rejects_malformed_lines(self):
        machine = make_machine()
        for line in ["bogus x", "open javascript:alert(1)", "open http://a\x1b]52;c;x", "open relative/path", "ports 80/4/x"]:
            with self.subTest(line=line), self.assertRaises(bifrost.ProtocolError):
                machine.handle_remote_line(line)

    def test_fatal_line_returns_reason(self):
        self.assertEqual(make_machine().handle_remote_line("fatal needs Linux"), "needs Linux")


class OpenTest(unittest.TestCase):
    def setUp(self):
        self.opened = []
        self.original_open_locally = bifrost.open_locally
        bifrost.open_locally = self.opened.append

    def tearDown(self):
        bifrost.open_locally = self.original_open_locally

    def test_remote_page_opens_at_route_url_and_rate_limits(self):
        pending_opens = RecordedPendingOpens()
        machine = make_machine(pending_opens)
        machine.routed[5173] = bifrost.Listener(5173, bifrost.Family.IPV4, "node")
        for _ in range(bifrost.AUTO_OPENS_PER_MINUTE + 1):
            machine.open_here("http://localhost:5173/a?b=1")
        self.assertEqual(len(self.opened), bifrost.AUTO_OPENS_PER_MINUTE)
        self.assertEqual(self.opened[0], "http://5173.afk-agents.localhost/a?b=1")
        self.assertEqual(pending_opens.urls, ["http://5173.afk-agents.localhost/a?b=1"])

    def test_external_page_waits_for_a_yes(self):
        pending_opens = RecordedPendingOpens()
        make_machine(pending_opens).open_here("https://example.com/login")
        self.assertEqual(self.opened, [])
        self.assertEqual(pending_opens.urls, ["https://example.com/login"])


    def test_clicked_link_opens_on_selected_machine_or_here(self):
        snapshot = {"machines": [
            {"is_selected": False, "ports": [{"port": 3000, "route_url": "http://3000.other.localhost"}]},
            {"is_selected": True, "ports": [{"port": 5173, "route_url": "http://5173.afk.localhost"}]},
        ]}
        original_read_status = bifrost.read_status
        bifrost.read_status = lambda: snapshot
        try:
            for clicked in ["http://localhost:5173/docs?q=1", "http://localhost:3000/", "http://127.0.0.1:5173/"]:
                bifrost.open_clicked_link(clicked)
        finally:
            bifrost.read_status = original_read_status
        self.assertEqual(self.opened, ["http://5173.afk.localhost/docs?q=1", "http://localhost:3000/", "http://5173.afk.localhost/"])


class FileLinksTest(unittest.TestCase):
    def setUp(self):
        self.file_links = bifrost.FileLinks(80)

    def request_path(self, url):
        return "/" + url.split(".localhost/", 1)[1]

    def test_round_trip(self):
        url = self.file_links.url("afk", "/home/me/site/index.html")
        self.assertTrue(url.startswith("http://files.afk.localhost/"))
        self.assertEqual(self.file_links.resolve("afk", self.request_path(url)), ("/home/me/site", "/home/me/site/index.html"))

    def test_relative_link_and_folder_index(self):
        path = self.request_path(self.file_links.url("afk", "/home/me/site/index.html"))
        folder_path = path.rsplit("/", 1)[0]
        self.assertEqual(self.file_links.resolve("afk", folder_path + "/css/app.css")[1], "/home/me/site/css/app.css")
        self.assertEqual(self.file_links.resolve("afk", folder_path + "/docs/")[1], "/home/me/site/docs/index.html")

    def test_refuses_forgery_and_escape(self):
        path = self.request_path(self.file_links.url("afk", "/home/me/site/index.html"))
        signature, folder, _ = path.lstrip("/").split("/", 2)
        forged_folder = "%2Fetc"
        self.assertIsNone(self.file_links.resolve("afk", f"/{signature}/{forged_folder}/passwd"))
        self.assertIsNone(self.file_links.resolve("other", path))
        self.assertIsNone(self.file_links.resolve("afk", f"/{signature}/{folder}/../../.ssh/id_ed25519"))
        self.assertIsNone(self.file_links.resolve("afk", f"/{signature}/{folder}/%2E%2E/secret"))
        self.assertIsNone(self.file_links.resolve("afk", f"/{'0' * 24}/{folder}/index.html"))


class NamingTest(unittest.TestCase):
    def test_machine_hostname(self):
        self.assertEqual(bifrost.machine_hostname("AFK Agents"), "afk-agents")
        self.assertEqual(bifrost.machine_hostname("--"), "machine")

    def test_route_url(self):
        self.assertEqual(bifrost.route_url("afk", 5173, 80), "http://5173.afk.localhost")
        self.assertEqual(bifrost.route_url("afk", 5173, 8080), "http://5173.afk.localhost:8080")

    def test_request_host(self):
        self.assertEqual(bifrost.request_host(b"GET / HTTP/1.1\r\nHOST: 5173.Afk.localhost\r\n\r\n"), "5173.afk.localhost")

    def test_printable_strips_escape_sequences(self):
        self.assertNotIn("\x1b", bifrost.printable("a\x1b[31mb\x07"))


class SettingsTest(unittest.TestCase):
    def tearDown(self):
        bifrost.CONFIG_FILE.unlink(missing_ok=True)

    def test_defaults(self):
        settings = bifrost.Settings.load()
        self.assertEqual((settings.min_port, settings.router_port), (bifrost.DEFAULT_MIN_PORT, bifrost.DEFAULT_ROUTER_PORT))

    def test_unknown_key_names_valid_keys(self):
        bifrost.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        bifrost.CONFIG_FILE.write_text('{"min_prot": 3000}')
        with self.assertRaisesRegex(SystemExit, "valid keys"):
            bifrost.Settings.load()

    def test_rejects_invalid_config_types_and_ranges(self):
        import json

        for configuration in [[], {"router_port": 0}, {"min_port": True}, {"ignored_ports": [70000]},
                              {"ignored_process_prefixes": "node"}, {"trusted_peers": ["bad"]}, {"log_level": []}]:
            bifrost.CONFIG_FILE.write_text(json.dumps(configuration))
            with self.subTest(configuration=configuration), self.assertRaises(SystemExit):
                bifrost.Settings.load()

    def test_ignored_listener(self):
        bifrost.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        bifrost.CONFIG_FILE.write_text('{"ignored_ports": [9119]}')
        settings = bifrost.Settings.load()
        self.assertTrue(settings.is_ignored(bifrost.Listener(9119, bifrost.Family.IPV4, "x")))
        self.assertTrue(settings.is_ignored(bifrost.Listener(5000, bifrost.Family.IPV4, "code-server")))
        self.assertFalse(settings.is_ignored(bifrost.Listener(5000, bifrost.Family.IPV4, "node")))


class ManifestTest(unittest.TestCase):
    def test_commands_name_files_that_exist(self):
        manifest = (PLUGIN_ROOT / "herdr-plugin.toml").read_text()
        for script in set(re.findall(r'command = \["sh", "([^"]+)"', manifest)):
            self.assertTrue((PLUGIN_ROOT / script).is_file(), script)
        self.assertEqual(re.search(r'^id = "([^"]+)"', manifest, re.MULTILINE).group(1), bifrost.PLUGIN_ID)

    def test_changelog_names_this_version(self):
        self.assertIn(f"## {bifrost.plugin_version()}", (PLUGIN_ROOT / "CHANGELOG.md").read_text())


class ShimTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="bifrost-test-home-"))

    def tearDown(self):
        shutil.rmtree(self.home)

    def run_shell_setup(self, operation, login_shell="/bin/bash"):
        return subprocess.run(
            ["sh", "-c", bifrost.REMOTE_SHELL_SETUP_SCRIPT, "bifrost-setup", operation],
            env={"HOME": str(self.home), "SHELL": login_shell, "PATH": os.environ["PATH"]},
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_shell_setup_adds_once_and_removes(self):
        bashrc = self.home / ".bashrc"
        bashrc.write_text("alias ll='ls -l'\n")
        self.assertEqual(self.run_shell_setup("add"), f"added {bashrc}")
        self.assertEqual(self.run_shell_setup("add"), f"present {bashrc}")
        self.assertIn('export BROWSER="$HOME/.local/share/bifrost/bin/xdg-open"', bashrc.read_text())
        self.assertEqual(self.run_shell_setup("remove"), f"removed {bashrc}")
        self.assertEqual(bashrc.read_text(), "alias ll='ls -l'\n")

    def test_shell_setup_picks_rc_by_login_shell(self):
        self.assertEqual(self.run_shell_setup("add", "/usr/bin/zsh"), f"added {self.home / '.zshenv'}")
        self.assertEqual(self.run_shell_setup("add", "/bin/dash"), f"added {self.home / '.profile'}")

    def test_shell_setup_leaves_symlinked_rc_alone(self):
        target = self.home / "dotfiles-bashrc"
        target.write_text("")
        (self.home / ".bashrc").symlink_to(target)
        self.assertEqual(self.run_shell_setup("add"), f"symlink {self.home / '.bashrc'}")
        self.assertEqual(target.read_text(), "")

    def test_opener_refresh_never_installs(self):
        opener = self.home / ".local/share/bifrost/bin/xdg-open"
        for mode in ["refresh", "create"]:
            subprocess.run(["sh", "-c", bifrost.REMOTE_OPENER_INSTALL_SCRIPT, "bifrost-install", mode],
                           input="#!/bin/sh\n# bifrost remote opener\n", env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
                           text=True, check=True)
            self.assertEqual(opener.exists(), mode == "create")
        self.assertTrue(bifrost.is_remote_opener(opener))
        self.assertTrue(bifrost.is_remote_opener(PLUGIN_ROOT / "remote-open.sh"))

    def test_cli_shim_runs_this_plugin(self):
        shim = self.home / "bin/bifrost"
        bifrost.write_cli_shim(shim)
        self.assertTrue(bifrost.is_cli_shim(shim))
        version = subprocess.run([str(shim), "--version"], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(version, f"bifrost {bifrost.plugin_version()}")


@unittest.skipUnless(IS_LINUX, "watch.sh and remote-open.sh read /proc and run on Linux machines")
class RemoteScriptTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="bifrost-test-home-"))
        self.environment = {"HOME": str(self.home), "PATH": os.environ["PATH"]}

    def tearDown(self):
        shutil.rmtree(self.home)

    def watch(self, ticks):
        lines = []
        with subprocess.Popen(
            ["sh", str(PLUGIN_ROOT / "watch.sh"), "1024", "test"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env=self.environment, text=True,
        ) as watcher:
            for _ in range(ticks):
                watcher.stdin.write("tick 1\n")
                watcher.stdin.flush()
                lines.append(watcher.stdout.readline().rstrip("\n"))
        return lines

    def test_watch_reports_own_loopback_listener(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            first_line = self.watch(1)[0]
        tokens = first_line.split()
        self.assertEqual(tokens[0], "ports")
        self.assertIn(f"{port}/4", [token.rsplit("/", 1)[0] for token in tokens[1:]])
        for token in tokens[1:]:
            bifrost.Listener.parse(token, 1024)

    def test_opener_queues_url_for_watcher_and_strips_control_characters(self):
        spool = self.home / ".cache/bifrost"
        spool.mkdir(parents=True)
        (spool / "watching.test").touch()
        subprocess.run(["sh", str(PLUGIN_ROOT / "remote-open.sh"), "http://localhost:5173/\x1b[31m"],
                       env=self.environment, check=True)
        lines = [line for line in self.watch(2) if line.startswith("open ")]
        self.assertEqual(lines, ["open http://localhost:5173/[31m"])

    def test_opener_falls_back_without_a_watcher(self):
        fake_bin = self.home / "bin"
        fake_bin.mkdir()
        fake_xdg_open = fake_bin / "xdg-open"
        fake_xdg_open.write_text('#!/bin/sh\necho "system xdg-open $1"\n')
        fake_xdg_open.chmod(0o755)
        environment = {**self.environment, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
        stale_heartbeat = self.home / ".cache/bifrost/watching.old"
        stale_heartbeat.parent.mkdir(parents=True)
        stale_heartbeat.touch()
        os.utime(stale_heartbeat, (time.time() - 60, time.time() - 60))
        completed = subprocess.run(["sh", str(PLUGIN_ROOT / "remote-open.sh"), "https://example.com"],
                                   env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), "system xdg-open https://example.com")


if __name__ == "__main__":
    unittest.main()
