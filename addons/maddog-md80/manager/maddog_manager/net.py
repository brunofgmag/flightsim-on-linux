from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

USER_AGENT = b"MaddogManager/1.0"


def request(url, extra=None):
    req = QNetworkRequest(QUrl(url))
    req.setRawHeader(b"User-Agent", USER_AGENT)
    req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    for name, value in (extra or {}).items():
        req.setRawHeader(name, value)
    return req


class Fetch(QObject):
    done = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, manager, url, parent=None):
        super().__init__(parent)
        self.reply = manager.get(request(url))
        self.reply.finished.connect(self.finish)

    def finish(self):
        reply = self.reply
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
        else:
            self.done.emit(bytes(reply.readAll()))
        self.deleteLater()


class FileDownload(QObject):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)
    canceled = pyqtSignal()

    def __init__(self, manager, url, destination, resume=False, parent=None):
        super().__init__(parent)
        self.destination = Path(destination)
        self.partial = self.destination.with_name(self.destination.name + ".part")
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.offset = self.partial.stat().st_size if resume and self.partial.exists() else 0
        self.handle = open(self.partial, "ab" if self.offset else "wb")
        self.stopped = False
        self.keep_partial = False
        headers = {b"Range": f"bytes={self.offset}-".encode()} if self.offset else None
        self.reply = manager.get(request(url, headers))
        self.reply.readyRead.connect(self.write)
        self.reply.downloadProgress.connect(self.report)
        self.reply.finished.connect(self.finish)
        self.reply.metaDataChanged.connect(self.check_resume)

    def check_resume(self):
        status = self.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if self.offset and status == 200:
            self.handle.seek(0)
            self.handle.truncate()
            self.offset = 0

    def write(self):
        self.handle.write(bytes(self.reply.readAll()))

    def report(self, received, total):
        self.progress.emit(self.offset + received, self.offset + total if total > 0 else -1)

    def cancel(self, keep_partial=False):
        self.stopped = True
        self.keep_partial = keep_partial
        self.reply.abort()

    def finish(self):
        self.write()
        self.handle.close()
        reply = self.reply
        reply.deleteLater()
        if self.stopped:
            if not self.keep_partial:
                self.partial.unlink(missing_ok=True)
            self.canceled.emit()
        elif reply.error() != QNetworkReply.NetworkError.NoError:
            self.failed.emit(reply.errorString())
        else:
            self.partial.replace(self.destination)
            self.done.emit(str(self.destination))
        self.deleteLater()
