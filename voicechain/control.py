"""Control channel for the running chain: Unix-domain socket, one JSON object per line.

Server side (:class:`ControlServer`) is driven by the runtime's service thread:
``poll()`` is called on every pass of that thread and never blocks (``selectors``
with a zero timeout). Each accepted connection sends one or more JSON lines; every
line is handed to ``handler(cmd) -> dict`` and the returned dict is written back as
one JSON line. Client side (:func:`send_command`) is used by ``python -m voicechain
ctl`` and by the MX Creative Console scripts.

Protocol details
----------------
* request:  ``{"cmd": "offset", "name": "room_db", "delta": 2}\\n``
* response: ``{"ok": true, "state": {...}, "message": "..."}\\n`` or
  ``{"ok": false, "error": "..."}\\n``
* A line that is not valid JSON or not a JSON object is answered with
  ``{"ok": false, "error": ...}``; the connection stays usable.
* The socket file is created with mode 0600 (only the user may steer the chain),
  a stale file from a crashed run is replaced at start and removed at stop.
"""

from __future__ import annotations

import getpass
import json
import os
import selectors
import socket
import stat
import time
from pathlib import Path
from typing import Callable

#: Default socket path when ``cfg.control.socket_path`` is not set: ``/tmp/voicechain-<user>.sock``.
#: Decision (docs/06 section 7): Unix socket paths are limited to 103 bytes on macOS and a
#: repository under ``/Users/<name>/Documents/...`` easily exceeds that with a repo-relative
#: path; ``/tmp`` is short, per-user (name in the file, mode 0600) and independent of where
#: the repository lives, so console scripts and ``ctl`` find it without configuration.
DEFAULT_SOCKET_TEMPLATE = "/tmp/voicechain-{user}.sock"
#: Longest Unix socket path accepted by macOS (``sun_path`` 104 bytes incl. NUL); Linux allows
#: 107, the stricter limit is used everywhere so a configuration works on both.
MAX_SOCKET_PATH_BYTES = 103
#: Timeout of the liveness probe that decides whether an existing socket file is stale.
LIVENESS_PROBE_S = 0.2
#: Commands handled per poll() call; more are left in the buffers for the next pass,
#: so a flood of commands cannot starve the adaptive controller and the status line.
MAX_COMMANDS_PER_POLL = 8
#: Longest accepted request line; anything larger is a protocol error, not a command.
MAX_LINE_BYTES = 64 * 1024
#: Pending-connection backlog of the listening socket; generous, because the server is
#: polled every few milliseconds and several console scripts may fire at once.
LISTEN_BACKLOG = 64
#: Bytes read per recv() on a client connection.
RECV_CHUNK = 4096
#: Socket mode: owner read/write only.
SOCKET_MODE = stat.S_IRUSR | stat.S_IWUSR


class ControlError(RuntimeError):
    """Client-side failure: no server, timeout, protocol error."""


def default_socket_path() -> Path:
    """``/tmp/voicechain-<user>.sock`` (user name, else numeric uid)."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = str(os.getuid())
    return Path(DEFAULT_SOCKET_TEMPLATE.format(user=user))


def socket_path_from_config(cfg: dict, base_dir: Path) -> Path:
    """Socket path from ``cfg["control"]["socket_path"]`` (default
    :func:`default_socket_path`); relative paths are resolved against ``base_dir``."""
    control_cfg = cfg.get("control") or {}
    raw = control_cfg.get("socket_path")
    if not raw:
        return default_socket_path()
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path


def check_socket_path(path) -> Path:
    """Raise :class:`ControlError` with a remedy when ``path`` is too long for AF_UNIX.

    Checked explicitly before bind()/connect(): Python reports the overflow itself
    as a bare ``OSError`` without errno, which would reach the user without advice.
    """
    path = Path(path)
    length = len(os.fsencode(str(path)))
    if length > MAX_SOCKET_PATH_BYTES:
        raise ControlError(
            "control socket path is {} bytes long, Unix sockets allow {} on macOS: {}. "
            "Set control.socket_path in the YAML to a short absolute path, e.g. "
            "~/.voicechain/ctl.sock or /tmp/voicechain-<user>.sock (the default), and give "
            "the same path to ctl with --socket or --config.".format(length, MAX_SOCKET_PATH_BYTES, path)
        )
    return path


def _encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _error_response(message: str) -> dict:
    return {"ok": False, "error": message}


class _Connection:
    """Per-client receive buffer and pending output."""

    __slots__ = ("sock", "inbuf", "outbuf", "closing")

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.inbuf = b""
        self.outbuf = b""
        self.closing = False


class ControlServer:
    """Non-blocking JSON-lines server on a Unix-domain socket.

    Parameters
    ----------
    socket_path:
        Path of the socket file. Its directory is created; an existing file is
        replaced (a server that died leaves one behind).
    handler:
        ``handler(cmd: dict) -> dict``; exceptions it raises are converted into
        ``{"ok": false, "error": ...}`` responses and counted in ``handler_errors``.
    max_commands_per_poll:
        Upper bound of handled commands per :meth:`poll`.
    """

    def __init__(
        self,
        socket_path,
        handler: Callable[[dict], dict],
        max_commands_per_poll: int = MAX_COMMANDS_PER_POLL,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self.max_commands_per_poll = int(max_commands_per_poll)
        if self.max_commands_per_poll < 1:
            raise ValueError("max_commands_per_poll must be >= 1")
        self._selector: selectors.BaseSelector | None = None
        self._listener: socket.socket | None = None
        self._connections: dict[int, _Connection] = {}
        self._bound_inode: int | None = None
        self.commands_handled = 0
        self.protocol_errors = 0
        self.handler_errors = 0

    # ------------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self) -> None:
        if self.running:
            raise RuntimeError("ControlServer is already running")
        check_socket_path(self.socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket_file()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.socket_path))
        except OSError as exc:
            listener.close()
            raise ControlError("cannot create control socket {}: {}".format(self.socket_path, exc)) from exc
        os.chmod(self.socket_path, SOCKET_MODE)
        self._bound_inode = os.stat(self.socket_path).st_ino
        listener.listen(LISTEN_BACKLOG)
        listener.setblocking(False)
        self._selector = selectors.DefaultSelector()
        self._selector.register(listener, selectors.EVENT_READ, data=None)
        self._listener = listener

    def stop(self) -> None:
        """Close every connection and the listener, remove the socket file (idempotent)."""
        for conn in list(self._connections.values()):
            self._close_connection(conn)
        self._connections.clear()
        if self._selector is not None and self._listener is not None:
            self._selector.unregister(self._listener)
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._selector is not None:
            self._selector.close()
            self._selector = None
        self._remove_own_socket_file()

    def _remove_own_socket_file(self) -> None:
        """Unlink the socket file only if it is still the one this server bound
        (another ``run`` may have replaced it in the meantime)."""
        if self._bound_inode is None:
            return
        try:
            current = os.lstat(self.socket_path)
        except FileNotFoundError:
            self._bound_inode = None
            return
        if stat.S_ISSOCK(current.st_mode) and current.st_ino == self._bound_inode:
            os.unlink(self.socket_path)
        self._bound_inode = None

    def _remove_stale_socket_file(self) -> None:
        """Replace a socket file left behind by a crashed run; refuse a live server or a
        regular file."""
        try:
            mode = os.lstat(self.socket_path).st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(mode):
            raise ControlError(
                "{} exists and is not a socket; refusing to replace it".format(self.socket_path)
            )
        if socket_is_alive(self.socket_path):
            raise ControlError(
                "voicechain run läuft bereits (Steuer-Socket {} antwortet). Zweite Instanz nicht "
                "gestartet; erst die laufende beenden.".format(self.socket_path)
            )
        os.unlink(self.socket_path)

    # ---------------------------------------------------------------------- poll
    def poll(self) -> int:
        """Accept connections, read requests, answer them. Returns handled commands.

        Never blocks: the selector is queried with timeout 0 and every socket is
        non-blocking. At most ``max_commands_per_poll`` commands are handled.
        """
        if self._selector is None or self._listener is None:
            return 0
        handled = 0
        for key, events in self._selector.select(timeout=0):
            if key.data is None:
                self._accept_pending()
                continue
            conn: _Connection = key.data
            if events & selectors.EVENT_READ:
                self._read_from(conn)
            if events & selectors.EVENT_WRITE:
                self._flush(conn)
        # Handle complete lines in arrival order across connections.
        for conn in list(self._connections.values()):
            while handled < self.max_commands_per_poll and b"\n" in conn.inbuf:
                line, _, conn.inbuf = conn.inbuf.partition(b"\n")
                conn.outbuf += _encode(self._dispatch(line))
                handled += 1
            if len(conn.inbuf) > MAX_LINE_BYTES:
                self.protocol_errors += 1
                conn.outbuf += _encode(_error_response("request line longer than {} bytes".format(MAX_LINE_BYTES)))
                conn.inbuf = b""
                conn.closing = True
            if conn.outbuf:
                self._flush(conn)
            if conn.closing and not conn.outbuf:
                self._close_connection(conn)
            if handled >= self.max_commands_per_poll:
                break
        self.commands_handled += handled
        return handled

    def _accept_pending(self) -> None:
        assert self._listener is not None and self._selector is not None
        while True:
            try:
                sock, _ = self._listener.accept()
            except (BlockingIOError, InterruptedError):
                return
            sock.setblocking(False)
            conn = _Connection(sock)
            self._connections[sock.fileno()] = conn
            self._selector.register(sock, selectors.EVENT_READ, data=conn)

    def _read_from(self, conn: _Connection) -> None:
        try:
            chunk = conn.sock.recv(RECV_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            # Peer vanished (reset / broken pipe): drop the connection quietly, it
            # cannot receive an answer any more.
            self._close_connection(conn)
            return
        if not chunk:
            # Orderly shutdown by the client; answer what is complete, then close.
            conn.closing = True
            return
        conn.inbuf += chunk

    def _dispatch(self, line: bytes) -> dict:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            self.protocol_errors += 1
            return _error_response("empty request line")
        try:
            cmd = json.loads(text)
        except json.JSONDecodeError as exc:
            self.protocol_errors += 1
            return _error_response("invalid JSON: {}".format(exc.msg))
        if not isinstance(cmd, dict):
            self.protocol_errors += 1
            return _error_response("request must be a JSON object, got {}".format(type(cmd).__name__))
        try:
            response = self.handler(cmd)
        except Exception as exc:  # noqa: BLE001 - a bad command must not kill the audio service thread
            self.handler_errors += 1
            return _error_response("{}: {}".format(type(exc).__name__, exc))
        if not isinstance(response, dict):
            self.handler_errors += 1
            return _error_response("handler returned {} instead of a dict".format(type(response).__name__))
        return response

    def _flush(self, conn: _Connection) -> None:
        if not conn.outbuf:
            return
        try:
            sent = conn.sock.send(conn.outbuf)
        except (BlockingIOError, InterruptedError):
            sent = 0
        except OSError:
            self._close_connection(conn)
            return
        conn.outbuf = conn.outbuf[sent:]
        assert self._selector is not None
        events = selectors.EVENT_READ | (selectors.EVENT_WRITE if conn.outbuf else 0)
        try:
            self._selector.modify(conn.sock, events, data=conn)
        except (KeyError, ValueError):
            # Connection was closed in between (peer error path); nothing to modify.
            return

    def _close_connection(self, conn: _Connection) -> None:
        fd = conn.sock.fileno()
        if self._selector is not None and fd >= 0:
            try:
                self._selector.unregister(conn.sock)
            except (KeyError, ValueError):
                # Not registered any more (double close on an error path).
                conn.sock.close()
                self._connections.pop(fd, None)
                return
        self._connections.pop(fd, None)
        conn.sock.close()


def socket_is_alive(path, timeout: float = LIVENESS_PROBE_S) -> bool:
    """True when a server accepts connections on ``path`` (used to tell a live run from
    a stale file). Connection refused / missing file / timeout mean "not alive"."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout, BlockingIOError, PermissionError):
        return False
    except OSError:
        return False
    else:
        return True
    finally:
        probe.close()


# --------------------------------------------------------------------------- client
#: Pause between connect retries while the server's backlog is full (several
#: console keys pressed at once); the server polls every 20 ms.
CONNECT_RETRY_S = 0.01


def _connect_with_retry(path: Path, timeout: float, deadline: float) -> socket.socket:
    """Connect to the Unix socket, retrying EAGAIN (full backlog) until ``deadline``."""
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
            return sock
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            sock.close()
            raise ControlError(
                "voicechain run läuft nicht (Socket {} antwortet nicht: {}). Alte Socket-Datei? "
                "'python -m voicechain run' neu starten.".format(path, exc)
            ) from exc
        except (BlockingIOError, InterruptedError) as exc:
            sock.close()
            if time.monotonic() >= deadline:
                raise ControlError(
                    "Verbindung zu {} nicht innerhalb von {:.1f} s möglich (Server überlastet)".format(path, timeout)
                ) from exc
            time.sleep(CONNECT_RETRY_S)
        except socket.timeout as exc:
            sock.close()
            raise ControlError("Verbindung zu {} nicht innerhalb von {:.1f} s möglich".format(path, timeout)) from exc


def send_command(cmd: dict, socket_path, timeout: float = 1.0) -> dict:
    """Send one command to the running chain and return its response dict.

    Raises :class:`ControlError` with a message suitable for the console when no
    server is listening (``voicechain run`` is not running), the server does not
    answer within ``timeout`` seconds, or the answer is not valid JSON.
    """
    if not isinstance(cmd, dict) or "cmd" not in cmd:
        raise ValueError("cmd must be a dict with a 'cmd' key, got {!r}".format(cmd))
    path = check_socket_path(socket_path)
    if not path.exists():
        raise ControlError(
            "voicechain run läuft nicht (kein Steuer-Socket unter {}). Erst 'python -m voicechain run' starten.".format(path)
        )
    deadline = time.monotonic() + timeout
    sock = _connect_with_retry(path, timeout, deadline)
    try:
        try:
            sock.sendall(_encode(cmd))
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(RECV_CHUNK)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_LINE_BYTES:
                    raise ControlError("response from {} longer than {} bytes".format(path, MAX_LINE_BYTES))
        except socket.timeout as exc:
            raise ControlError(
                "voicechain run hat innerhalb von {:.1f} s nicht geantwortet (Socket {})".format(timeout, path)
            ) from exc
        except OSError as exc:
            # Connection reset / broken pipe: the server went away mid-request.
            raise ControlError(
                "voicechain run hat die Verbindung ohne Antwort geschlossen (Socket {}: {})".format(path, exc)
            ) from exc
    finally:
        sock.close()
    if not data:
        raise ControlError("voicechain run hat die Verbindung ohne Antwort geschlossen (Socket {})".format(path))
    try:
        response = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlError("unreadable response from {}: {}".format(path, exc)) from exc
    if not isinstance(response, dict):
        raise ControlError("response from {} is not a JSON object".format(path))
    return response
