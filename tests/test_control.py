"""Tests for the control channel (voicechain.control) with a real Unix socket.

The server is polled from a background thread the way the runtime's service
thread does it; clients use ``send_command`` or raw sockets for protocol edge
cases. No audio hardware involved.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from pathlib import Path

import pytest

from voicechain.control import (
    DEFAULT_SOCKET_PATH,
    MAX_LINE_BYTES,
    ControlError,
    ControlServer,
    send_command,
    socket_path_from_config,
)


class EchoHandler:
    """Records commands; answers ok with the command echoed, or an error on request."""

    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, cmd: dict) -> dict:
        with self.lock:
            self.commands.append(cmd)
        if cmd.get("cmd") == "explode":
            raise ValueError("handler failure for testing")
        if cmd.get("cmd") == "deny":
            return {"ok": False, "error": "nicht erlaubt"}
        if cmd.get("cmd") == "slow":
            time.sleep(float(cmd.get("seconds", 0.0)))
        return {"ok": True, "echo": cmd, "message": "ok {}".format(cmd.get("cmd"))}


class PolledServer:
    """ControlServer polled from a thread every few milliseconds (like the runtime)."""

    def __init__(self, socket_path: Path, handler, poll_interval_s: float = 0.005, **kwargs) -> None:
        self.server = ControlServer(socket_path, handler, **kwargs)
        self.poll_interval_s = poll_interval_s
        self.polls = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "PolledServer":
        self.server.start()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.server.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.server.poll()
            self.polls += 1
            time.sleep(self.poll_interval_s)


@pytest.fixture
def sock_path(tmp_path) -> Path:
    # Unix socket paths are limited to ~100 bytes; keep the name short.
    return tmp_path / "c.sock"


# ------------------------------------------------------------------- lifecycle
def test_start_creates_private_socket_and_stop_removes_it(sock_path):
    handler = EchoHandler()
    server = ControlServer(sock_path, handler)
    assert not server.running
    server.start()
    try:
        assert server.running
        mode = os.stat(sock_path).st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o600
    finally:
        server.stop()
    assert not sock_path.exists()
    assert not server.running
    server.stop()  # idempotent


def test_stale_socket_file_is_replaced_but_regular_file_is_not(sock_path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()  # leaves the file behind like a crashed server
    assert sock_path.exists()
    server = ControlServer(sock_path, EchoHandler())
    server.start()
    try:
        with PolledServerFromRunning(server):
            assert send_command({"cmd": "status"}, sock_path)["ok"] is True
    finally:
        server.stop()

    regular = sock_path.parent / "file.sock"
    regular.write_text("not a socket", encoding="utf-8")
    with pytest.raises(ControlError) as excinfo:
        ControlServer(regular, EchoHandler()).start()
    assert "not a socket" in str(excinfo.value)
    assert regular.read_text(encoding="utf-8") == "not a socket"


class PolledServerFromRunning:
    """Poll an already started server from a thread for the duration of a block."""

    def __init__(self, server: ControlServer) -> None:
        self.server = server
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            self.server.poll()
            time.sleep(0.005)


def test_socket_path_too_long_is_a_clear_error(tmp_path):
    long_path = tmp_path / ("x" * 120) / "c.sock"
    with pytest.raises(ControlError) as excinfo:
        ControlServer(long_path, EchoHandler()).start()
    assert "too long" in str(excinfo.value)


def test_socket_path_from_config_defaults_and_resolves(tmp_path):
    base = tmp_path / "repo"
    assert socket_path_from_config({}, base) == base / DEFAULT_SOCKET_PATH
    assert socket_path_from_config({"control": {"socket_path": "run/x.sock"}}, base) == base / "run" / "x.sock"
    absolute = tmp_path / "abs.sock"
    assert socket_path_from_config({"control": {"socket_path": str(absolute)}}, base) == absolute


# ------------------------------------------------------------------- round trips
def test_round_trip_commands_and_responses(sock_path):
    handler = EchoHandler()
    with PolledServer(sock_path, handler) as polled:
        response = send_command({"cmd": "offset", "name": "room_db", "delta": 2}, sock_path)
        assert response["ok"] is True
        assert response["echo"] == {"cmd": "offset", "name": "room_db", "delta": 2}
        assert response["message"] == "ok offset"
        denied = send_command({"cmd": "deny"}, sock_path)
        assert denied == {"ok": False, "error": "nicht erlaubt"}
        exploded = send_command({"cmd": "explode"}, sock_path)
        assert exploded["ok"] is False and "ValueError" in exploded["error"]
        assert polled.server.commands_handled == 3
        assert polled.server.handler_errors == 1
        assert [c["cmd"] for c in handler.commands] == ["offset", "deny", "explode"]
        assert polled.polls > 0


def test_unicode_survives_both_directions(sock_path):
    with PolledServer(sock_path, EchoHandler()):
        response = send_command({"cmd": "marker", "text": "Präsentation – Ø Rückfrage"}, sock_path)
        assert response["echo"]["text"] == "Präsentation – Ø Rückfrage"


def test_broken_json_and_non_object_requests_get_error_responses(sock_path):
    with PolledServer(sock_path, EchoHandler()) as polled:
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(2.0)
        raw.connect(str(sock_path))
        raw.sendall(b'{"cmd": "status"\n')  # broken JSON
        first = _read_line(raw)
        assert first["ok"] is False and "invalid JSON" in first["error"]
        raw.sendall(b"[1, 2, 3]\n")  # valid JSON, wrong type
        second = _read_line(raw)
        assert second["ok"] is False and "JSON object" in second["error"]
        raw.sendall(b"\n")  # empty line
        third = _read_line(raw)
        assert third["ok"] is False and "empty" in third["error"]
        # the connection is still usable afterwards
        raw.sendall(b'{"cmd": "status"}\n')
        fourth = _read_line(raw)
        assert fourth["ok"] is True
        raw.close()
        assert polled.server.protocol_errors == 3
        assert polled.server.commands_handled == 4


def test_oversized_request_line_is_rejected(sock_path):
    with PolledServer(sock_path, EchoHandler()) as polled:
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(2.0)
        raw.connect(str(sock_path))
        raw.sendall(b"x" * (MAX_LINE_BYTES + 10))  # no newline, far too long
        response = _read_line(raw)
        assert response["ok"] is False and "longer than" in response["error"]
        raw.close()
        assert polled.server.protocol_errors == 1


def _read_line(sock: socket.socket) -> dict:
    """Read exactly one response line (several may arrive in one recv)."""
    reader = _readers.setdefault(sock, sock.makefile("rb"))
    line = reader.readline()
    return json.loads(line.decode("utf-8"))


_readers: dict = {}


def test_many_clients_in_parallel_all_get_their_own_answer(sock_path):
    handler = EchoHandler()
    with PolledServer(sock_path, handler, max_commands_per_poll=3) as polled:
        results: dict[int, dict] = {}
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                results[i] = send_command({"cmd": "offset", "id": i}, sock_path, timeout=5.0)
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        assert not errors
        assert sorted(results) == list(range(20))
        for i, response in results.items():
            assert response["ok"] is True and response["echo"]["id"] == i
        assert polled.server.commands_handled == 20
        assert len(handler.commands) == 20


def test_poll_handles_at_most_max_commands_per_call(sock_path):
    server = ControlServer(sock_path, EchoHandler(), max_commands_per_poll=2)
    server.start()
    try:
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(2.0)
        raw.connect(str(sock_path))
        raw.sendall(b'{"cmd": "a"}\n{"cmd": "b"}\n{"cmd": "c"}\n{"cmd": "d"}\n{"cmd": "e"}\n')
        time.sleep(0.05)
        counts = []
        total = 0
        deadline = time.monotonic() + 2.0
        while total < 5 and time.monotonic() < deadline:
            n = server.poll()
            counts.append(n)
            total += n
            time.sleep(0.005)
        assert total == 5
        assert max(counts) == 2  # never more than the configured bound in one poll
        answers = [_read_line(raw) for _ in range(5)]
        assert [a["echo"]["cmd"] for a in answers] == ["a", "b", "c", "d", "e"]
        raw.close()
    finally:
        server.stop()


def test_poll_does_not_block_without_clients(sock_path):
    server = ControlServer(sock_path, EchoHandler())
    server.start()
    try:
        t0 = time.perf_counter()
        for _ in range(100):
            assert server.poll() == 0
        assert time.perf_counter() - t0 < 0.5  # 100 idle polls are essentially free
    finally:
        server.stop()


def test_poll_before_start_and_after_stop_is_a_noop(sock_path):
    server = ControlServer(sock_path, EchoHandler())
    assert server.poll() == 0
    server.start()
    server.stop()
    assert server.poll() == 0


def test_invalid_max_commands_rejected(sock_path):
    with pytest.raises(ValueError):
        ControlServer(sock_path, EchoHandler(), max_commands_per_poll=0)


# ------------------------------------------------------------------- client errors
def test_client_reports_missing_server_clearly(sock_path):
    with pytest.raises(ControlError) as excinfo:
        send_command({"cmd": "status"}, sock_path)
    assert "voicechain run läuft nicht" in str(excinfo.value)
    assert str(sock_path) in str(excinfo.value)


def test_client_reports_stale_socket_file_clearly(sock_path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    with pytest.raises(ControlError) as excinfo:
        send_command({"cmd": "status"}, sock_path)
    assert "voicechain run läuft nicht" in str(excinfo.value)
    assert "neu starten" in str(excinfo.value)


def test_client_times_out_when_server_does_not_poll(sock_path):
    server = ControlServer(sock_path, EchoHandler())
    server.start()  # listening, but nobody polls -> no answer
    try:
        t0 = time.perf_counter()
        with pytest.raises(ControlError) as excinfo:
            send_command({"cmd": "status"}, sock_path, timeout=0.3)
        elapsed = time.perf_counter() - t0
        assert "nicht geantwortet" in str(excinfo.value)
        assert 0.25 <= elapsed < 2.0
    finally:
        server.stop()


def test_client_rejects_malformed_command_locally(sock_path):
    with pytest.raises(ValueError):
        send_command({"name": "room_db"}, sock_path)  # no "cmd"
    with pytest.raises(ValueError):
        send_command("status", sock_path)  # type: ignore[arg-type]


def test_client_reports_server_that_closes_without_answer(sock_path):
    """A listener that accepts and closes immediately (no JSON) is reported, not a traceback."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    listener.listen(1)

    def closer():
        conn, _ = listener.accept()
        conn.close()

    thread = threading.Thread(target=closer, daemon=True)
    thread.start()
    try:
        with pytest.raises(ControlError) as excinfo:
            send_command({"cmd": "status"}, sock_path, timeout=2.0)
        assert "ohne Antwort" in str(excinfo.value)
    finally:
        thread.join(timeout=2.0)
        listener.close()
        sock_path.unlink(missing_ok=True)


# ------------------------------------------------------------- console manifest
MANIFEST_DIR = Path(__file__).resolve().parent.parent / "integrations" / "mx_console"
EXPECTED_PAGES = {
    "Meeting": ["room-less", "room-more", "deess-more", "natural", "dense", "freeze", "reset", "status"],
    "Feinabstimmung": ["warmer", "brighter", "deess-less", "loudness-reset", "relearn", "snapshot", "bypass", "marker"],
}
EXPECTED_DIAL = {"rotate_cw": "louder", "rotate_ccw": "quieter", "press": "mute"}


def _load_manifest() -> dict:
    return json.loads((MANIFEST_DIR / "functions.json").read_text(encoding="utf-8"))


def test_manifest_pages_match_the_assignment():
    manifest = _load_manifest()
    assert [page["name"] for page in manifest["pages"]] == list(EXPECTED_PAGES)
    for page in manifest["pages"]:
        keys = page["keys"]
        assert [k["slot"] for k in keys] == list(range(1, 9))  # 8 keys per page
        assert [k["id"] for k in keys] == EXPECTED_PAGES[page["name"]]
        assert {k: v["id"] for k, v in page["dial"].items()} == EXPECTED_DIAL  # dial identical on both pages


def test_every_function_has_script_applescript_cli_and_short_label():
    from voicechain.cli import CTL_COMMANDS, build_parser

    parser = build_parser()
    manifest = _load_manifest()
    entries = [k for page in manifest["pages"] for k in page["keys"]] + [
        v for page in manifest["pages"] for v in page["dial"].values()
    ]
    ids = set()
    for entry in entries:
        ids.add(entry["id"])
        assert 1 <= len(entry["label"]) <= 10, entry["label"]
        assert entry["description"]
        script = MANIFEST_DIR.parent.parent / entry["action_script"]
        applescript = MANIFEST_DIR.parent.parent / entry["applescript"]
        assert script.is_file(), script
        assert os.access(script, os.X_OK), "not executable: {}".format(script)
        assert applescript.is_file(), applescript
        assert "-m voicechain ctl {} --notify".format(entry["id"]) in script.read_text(encoding="utf-8")
        assert "actions/{}.sh".format(entry["id"]) in applescript.read_text(encoding="utf-8")
        assert entry["cli"] == "python -m voicechain ctl {} --notify".format(entry["id"])
        args = parser.parse_args(["ctl", entry["id"]])  # the CLI name exists in the parser
        assert args.name == entry["id"]
    assert ids == set(CTL_COMMANDS)  # all 19 commands are reachable from the console (17 functions + dial)
    assert len(ids) == 19


def test_action_scripts_are_valid_bash_and_documented():
    import subprocess

    scripts = sorted((MANIFEST_DIR / "actions").glob("*.sh"))
    assert len(scripts) == 19
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
        text = script.read_text(encoding="utf-8")
        assert "set -euo pipefail" in text
        assert ".venv/bin/python" in text
    docs = (MANIFEST_DIR.parent.parent / "docs" / "05_MX_Creative_Console.md").read_text(encoding="utf-8")
    readme = (MANIFEST_DIR / "README.md").read_text(encoding="utf-8")
    for script in scripts:
        name = script.stem
        assert "`{}`".format(name) in docs, "docs/05 has no row for {}".format(name)
        assert name in readme
