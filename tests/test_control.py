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
    MAX_LINE_BYTES,
    MAX_SOCKET_PATH_BYTES,
    ControlError,
    ControlServer,
    check_socket_path,
    default_socket_path,
    send_command,
    socket_is_alive,
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
def sock_path() -> Path:
    """Short socket path: macOS limits AF_UNIX paths to 103 bytes and pytest's tmp_path
    (/private/var/folders/...) exceeds that, so sockets live in /tmp/vcXXXX/c.sock."""
    import shutil
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="vc", dir="/tmp"))
    path = folder / "c.sock"
    assert len(os.fsencode(str(path))) <= 90
    try:
        yield path
    finally:
        shutil.rmtree(folder, ignore_errors=True)


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


def test_socket_path_too_long_is_checked_before_bind_and_connect(tmp_path):
    long_path = tmp_path / ("x" * 120) / "c.sock"
    assert len(os.fsencode(str(long_path))) > MAX_SOCKET_PATH_BYTES
    # server side: explicit check, with the remedy, before any bind()
    with pytest.raises(ControlError) as excinfo:
        ControlServer(long_path, EchoHandler()).start()
    message = str(excinfo.value)
    assert "bytes long" in message and str(MAX_SOCKET_PATH_BYTES) in message
    assert "control.socket_path" in message and "/tmp/voicechain-<user>.sock" in message
    assert not long_path.parent.exists()  # nothing was created
    # client side: same check before connect(), same advice
    with pytest.raises(ControlError) as excinfo:
        send_command({"cmd": "status"}, long_path)
    assert "control.socket_path" in str(excinfo.value)
    # exactly at the limit is accepted by the check itself
    at_limit = Path("/" + "y" * (MAX_SOCKET_PATH_BYTES - 1))
    assert check_socket_path(at_limit) == at_limit


def test_socket_path_from_config_defaults_and_resolves(tmp_path):
    base = tmp_path / "repo"
    default = socket_path_from_config({}, base)
    assert default == default_socket_path()
    assert str(default).startswith("/tmp/voicechain-") and str(default).endswith(".sock")
    assert len(os.fsencode(str(default))) <= MAX_SOCKET_PATH_BYTES
    assert socket_path_from_config({"control": {"socket_path": None}}, base) == default
    assert socket_path_from_config({"control": {"socket_path": "run/x.sock"}}, base) == base / "run" / "x.sock"
    absolute = tmp_path / "abs.sock"
    assert socket_path_from_config({"control": {"socket_path": str(absolute)}}, base) == absolute


def test_second_server_refuses_to_replace_a_live_socket(sock_path):
    first = EchoHandler()
    with PolledServer(sock_path, first) as running:
        assert socket_is_alive(sock_path)
        second = ControlServer(sock_path, EchoHandler())
        with pytest.raises(ControlError) as excinfo:
            second.start()
        assert "voicechain run läuft bereits" in str(excinfo.value)
        assert not second.running
        second.stop()  # never bound: must not touch the first server's socket file
        assert sock_path.exists()
        assert send_command({"cmd": "status"}, sock_path)["ok"] is True  # first server untouched
        assert running.server.commands_handled == 1
    assert not socket_is_alive(sock_path)


def test_stop_removes_only_its_own_socket_file(sock_path):
    server = ControlServer(sock_path, EchoHandler())
    server.start()
    # Another process replaces the file under us (a second run that ignored ours): unlink + rebind.
    os.unlink(sock_path)
    other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    other.bind(str(sock_path))
    try:
        server.stop()
        assert sock_path.exists()  # the newer file belongs to someone else and stays
        assert stat.S_ISSOCK(os.lstat(sock_path).st_mode)
    finally:
        other.close()
        sock_path.unlink(missing_ok=True)


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
    "Meeting": ["room-less", "room-more", "deess-more", "deess-less", "natural", "dense", "freeze", "reset"],
    "Feinabstimmung": ["warmer", "brighter", "status", "power", "relearn", "snapshot", "bypass", "mute"],
}
EXPECTED_DIAL = {"rotate_cw": "louder", "rotate_ccw": "quieter", "press": "loudness-reset"}
EXPECTED_UNASSIGNED = ["marker"]  # keeps script, AppleScript and CLI, but no key
EXPECTED_LABELS = {"deess-more": "Zischen −", "deess-less": "Zischen +", "power": "DSP an/aus", "loudness-reset": "Lautheit 0"}


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
    assert [u["id"] for u in manifest["unassigned"]] == EXPECTED_UNASSIGNED
    assert all("slot" not in u for u in manifest["unassigned"])
    labels = {e["id"]: e["label"] for page in manifest["pages"] for e in page["keys"] + list(page["dial"].values())}
    for fid, label in EXPECTED_LABELS.items():
        assert labels[fid] == label
    # dial detents must not raise a notification banner each, only rejections
    for page in manifest["pages"]:
        for name, entry in page["dial"].items():
            expected_flag = "--notify-errors" if name.startswith("rotate") else "--notify"
            assert entry["cli"].endswith(expected_flag), (name, entry["cli"])


def test_every_function_has_script_applescript_cli_and_short_label():
    from voicechain.cli import CTL_COMMANDS, build_parser

    parser = build_parser()
    manifest = _load_manifest()
    entries = (
        [k for page in manifest["pages"] for k in page["keys"]]
        + [v for page in manifest["pages"] for v in page["dial"].values()]
        + list(manifest["unassigned"])  # unassigned functions need the same artefacts
    )
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
        flag = "--notify-errors" if entry["id"] in ("louder", "quieter") else "--notify"
        script_text = script.read_text(encoding="utf-8")
        assert "-m voicechain ctl {} {} ".format(entry["id"], flag) in script_text
        assert "VOICECHAIN_PYTHON" in script_text and "/opt/homebrew/bin/python3" in script_text
        assert "actions/{}.sh".format(entry["id"]) in applescript.read_text(encoding="utf-8")
        assert entry["cli"] == "python -m voicechain ctl {} {}".format(entry["id"], flag)
        args = parser.parse_args(["ctl", entry["id"]])  # the CLI name exists in the parser
        assert args.name == entry["id"]
    assert ids == set(CTL_COMMANDS)  # every CLI command has console artefacts (keys, dial or unassigned)
    assert len(ids) == 20


def test_action_scripts_are_valid_bash_and_documented():
    import subprocess

    scripts = sorted((MANIFEST_DIR / "actions").glob("*.sh"))
    assert len(scripts) == 20
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
