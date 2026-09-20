from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstanceGuard(QObject):
    """Own a local IPC endpoint and notify the primary application on relaunch."""

    activation_requested = Signal()

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._owns_server = False

    def acquire(self) -> bool:
        if self._notify_existing():
            return False

        # A crashed process can leave a stale Unix socket. On Windows this is a
        # harmless no-op once the named pipe has disappeared.
        QLocalServer.removeServer(self.name)
        if self._server.listen(self.name):
            self._owns_server = True
            return True

        # Another process may have won the small race between probe and listen.
        self._notify_existing(timeout_ms=400)
        return False

    def _notify_existing(self, timeout_ms: int = 180) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout_ms):
            socket.abort()
            return False
        socket.write(b"activate\n")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def _accept_connections(self) -> None:
        received = False
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            received = True
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()
        if received:
            self.activation_requested.emit()

    def close(self) -> None:
        if not self._owns_server:
            return
        self._server.close()
        QLocalServer.removeServer(self.name)
        self._owns_server = False
