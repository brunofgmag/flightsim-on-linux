import argparse
import os
import sys

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QNetworkAccessManager
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QStackedWidget, QWidget

from .home_page import HomePage
from .liveries_page import LiveriesPage
from .locations import MaddogFiles, MaddogNotFound, resolve
from .options_page import OptionsPage

APP_NAME = "Maddog Manager"
DESKTOP_FILE = "flightsim-on-linux-maddog-md80"


class MainWindow(QMainWindow):
    def __init__(self, files):
        super().__init__()
        self.files = files
        self.setWindowTitle(APP_NAME)
        self.resize(1240, 820)
        self.network = QNetworkAccessManager(self)
        self.pages = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setFixedWidth(180)
        self.nav.setIconSize(QSize(22, 22))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.liveries = LiveriesPage(files, self.network)
        self.add_page("Home", "go-home", HomePage(files, self.network))
        self.add_page("Liveries", "preferences-desktop-theme", self.liveries)
        self.add_page("Options", "configure", OptionsPage(files))
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self.nav)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

    def add_page(self, title, icon, page):
        item = QListWidgetItem(QIcon.fromTheme(icon), title)
        self.nav.addItem(item)
        self.pages.addWidget(page)
        if hasattr(page, "message"):
            page.message.connect(lambda text: self.statusBar().showMessage(text, 15000))

    def closeEvent(self, event):
        if self.liveries.busy():
            answer = QMessageBox.question(self, APP_NAME, "A download or install is in progress. Close anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="maddog-manager")
    parser.add_argument("--prefix", help="Wine prefix of MSFS 2024 (default: the one flightsim-on-linux finds)")
    args, qt_args = parser.parse_known_args(argv)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(DESKTOP_FILE)
    app.setWindowIcon(QIcon.fromTheme(DESKTOP_FILE))
    try:
        files = MaddogFiles.discover(args.prefix, os.environ.get("FSOL_COMMUNITY"))
    except MaddogNotFound as error:
        QMessageBox.critical(None, APP_NAME, f"The Maddog Manager cannot open: {error}.")
        return 1
    original = resolve(files.root, ["Maddog Manager", "Assets", "leonardo_2024.ico"])
    if app.windowIcon().isNull() and original.is_file():
        app.setWindowIcon(QIcon(str(original)))
    window = MainWindow(files)
    window.show()
    return app.exec()
