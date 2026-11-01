import subprocess
from pathlib import Path

from PyQt6.QtCore import QStandardPaths, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from . import liveries
from .ini import IniFile
from .liveries import CATALOG_URL, PAINT_KIT_URL, VARIANT_NAMES, Livery
from .net import Fetch, FileDownload
from .sim import simulator_running
from .widgets import FlowLayout, Tasks, expanding

CARD_WIDTH = 272
THUMB_SIZE = (248, 140)
OFFICIAL, LOCAL, BROWSE = range(3)


def cache_dir(name):
    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)) / "maddog-manager" / name
    base.mkdir(parents=True, exist_ok=True)
    return base


def downloads_dir():
    try:
        folder = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        folder = ""
    return Path(folder) if folder else Path.home() / "Downloads"


def megabytes(size):
    return f"{size / 1048576:.0f} MB"


class LiveryCard(QFrame):
    action = pyqtSignal(object)
    cancel = pyqtSignal(object)

    def __init__(self, livery, external=False):
        super().__init__()
        self.livery = livery
        self.external = external
        self.installed = False
        self.installed_variant = -1
        self.busy = False
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(CARD_WIDTH)
        layout = QVBoxLayout(self)
        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(*THUMB_SIZE)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setText("no image")
        self.thumbnail.setStyleSheet("color: palette(placeholder-text); background: palette(alternate-base); border-radius: 4px;")
        self.name = QLabel(livery.name)
        self.name.setWordWrap(True)
        font = self.name.font()
        font.setBold(True)
        self.name.setFont(font)
        self.detail = QLabel()
        self.detail.setStyleSheet("color: palette(placeholder-text);")
        self.status = QLabel()
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        buttons = QHBoxLayout()
        self.button = QPushButton()
        self.button.clicked.connect(lambda: self.action.emit(self))
        self.stop = QPushButton("Cancel")
        self.stop.clicked.connect(lambda: self.cancel.emit(self))
        self.stop.hide()
        buttons.addWidget(self.button)
        buttons.addWidget(self.stop)
        for widget in (self.thumbnail, self.name, self.detail, self.status, self.progress):
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.refresh()

    def set_image(self, pixmap):
        if pixmap.isNull():
            return
        self.thumbnail.setPixmap(pixmap.scaled(*THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def set_installed(self, installed, variant):
        self.installed = installed
        self.installed_variant = variant
        self.refresh()

    def set_busy(self, text, can_cancel=False):
        self.busy = True
        self.status.setText(text)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.button.hide()
        self.stop.setVisible(can_cancel)

    def set_progress(self, done, total):
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
            self.progress.setFormat(f"{megabytes(done)} of {megabytes(total)}")
        else:
            self.progress.setRange(0, 0)

    def set_idle(self):
        self.busy = False
        self.progress.hide()
        self.stop.hide()
        self.button.show()
        self.refresh()

    def refresh(self):
        parts = [self.livery.reg]
        if self.livery.variant_name:
            parts.append(self.livery.variant_name)
        elif self.installed_variant in VARIANT_NAMES:
            parts.append(VARIANT_NAMES[self.installed_variant])
        if self.external:
            parts.append("not in the catalog")
        self.detail.setText(" · ".join(parts))
        if self.busy:
            return
        if self.installed:
            self.status.setText("Installed")
            self.status.setStyleSheet("color: #059669; font-weight: bold;")
            self.button.setText("Remove")
        else:
            self.status.setText("Not installed")
            self.status.setStyleSheet("color: palette(placeholder-text);")
            self.button.setText("Install")

    def matches(self, query, status):
        if query and query.lower() not in self.livery.name.lower() and query.lower() not in self.livery.reg.lower():
            return False
        if status == 1:
            return self.installed
        if status == 2:
            return not self.installed
        return True


class LiveriesPage(QWidget):
    message = pyqtSignal(str)

    def __init__(self, files, network):
        super().__init__()
        self.files = files
        self.network = network
        self.tasks = Tasks()
        self.cards = []
        self.downloads = {}
        self.paint_kit = None
        self.loading = 0
        manager = IniFile(files.manager_ini)
        self.local_folder = Path(manager.get("MANAGER", "localPath") or downloads_dir())

        self.source = QComboBox()
        self.source.addItems(["Official liveries", "", "Choose folder…"])
        self.update_local_label()
        self.source.setCurrentIndex(LOCAL if manager.get_int("MANAGER", "Liveries") > 0 else OFFICIAL)
        self.source.activated.connect(self.source_chosen)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name or registration")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filters)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "Installed", "Not installed"])
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        reload = QToolButton()
        reload.setText("Refresh")
        reload.clicked.connect(self.load)
        top = QHBoxLayout()
        top.addWidget(self.source)
        top.addWidget(expanding(self.search))
        top.addWidget(self.status_filter)
        top.addWidget(reload)

        self.kit_button = QPushButton("Download paint kit")
        self.kit_button.clicked.connect(self.paint_kit_clicked)
        self.kit_pause = QPushButton("Pause")
        self.kit_pause.clicked.connect(lambda: self.stop_paint_kit(True))
        self.kit_cancel = QPushButton("Cancel")
        self.kit_cancel.clicked.connect(lambda: self.stop_paint_kit(False))
        self.kit_progress = QProgressBar()
        self.kit_show = QPushButton("Show in folder")
        self.kit_show.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(downloads_dir()))))
        self.count = QLabel()
        bottom = QHBoxLayout()
        bottom.addWidget(self.count)
        bottom.addStretch(1)
        for widget in (self.kit_progress, self.kit_pause, self.kit_cancel, self.kit_show, self.kit_button):
            bottom.addWidget(widget)
        self.reset_paint_kit()

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setStyleSheet("color: palette(placeholder-text); font-size: 14pt;")
        self.grid_host = QWidget()
        self.grid = FlowLayout(self.grid_host)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_host)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.empty, 1)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(bottom)
        self.load()

    def save_manager(self, key, value):
        ini = IniFile(self.files.manager_ini)
        if self.files.manager_ini.is_file():
            ini.set("MANAGER", key, value)
            ini.save()

    def update_local_label(self):
        self.source.setItemText(LOCAL, f"Local folder ({self.local_folder})")

    def source_chosen(self, index):
        if index == BROWSE:
            folder = QFileDialog.getExistingDirectory(self, "Folder with livery zips", str(self.local_folder))
            if folder:
                self.local_folder = Path(folder)
                self.save_manager("localPath", str(self.local_folder))
                self.update_local_label()
            self.source.setCurrentIndex(LOCAL)
        self.save_manager("Liveries", 1 if self.source.currentIndex() == LOCAL else 0)
        self.load()

    def clear(self):
        for card in self.cards:
            if card.livery.reg in self.downloads:
                self.downloads.pop(card.livery.reg).cancel()
            self.grid.removeWidget(card)
            card.deleteLater()
        self.cards = []

    def show_empty(self, text):
        self.empty.setText(text)
        self.empty.setVisible(bool(text))
        self.scroll.setVisible(not text)

    def load(self):
        if any(card.busy for card in self.cards):
            self.message.emit("Wait for the install in progress to finish before refreshing the list.")
            return
        self.clear()
        self.loading += 1
        generation = self.loading
        if self.source.currentIndex() == OFFICIAL:
            self.show_empty("Downloading the list of official liveries…")
            fetch = Fetch(self.network, CATALOG_URL, self)
            fetch.done.connect(lambda data: self.catalog_loaded(generation, data))
            fetch.failed.connect(lambda error: self.load_failed(generation, f"Could not download the livery list: {error}"))
        else:
            folder = self.local_folder
            self.show_empty(f"Reading the zips in {folder}…")
            cache = cache_dir("local")
            self.tasks.start(
                lambda progress: [livery for livery in (liveries.read_zip_livery(path, cache / path.stem) for path in liveries.local_zips(folder)) if livery],
                on_done=lambda found: self.local_loaded(generation, found),
                on_failed=lambda error: self.load_failed(generation, f"Could not read {folder}: {error}"),
            )

    def load_failed(self, generation, text):
        if generation == self.loading:
            self.show_empty(text)
            self.message.emit(text)

    def catalog_loaded(self, generation, data):
        if generation != self.loading:
            return
        try:
            found = liveries.parse_catalog(data)
        except (ValueError, KeyError, TypeError) as error:
            self.load_failed(generation, f"The livery list came in an unexpected format: {error}")
            return
        self.populate(found)

    def local_loaded(self, generation, found):
        if generation != self.loading:
            return
        if not found:
            self.show_empty(f"No livery zip in {self.local_folder}")
            self.populate([])
            return
        self.populate(found)

    def populate(self, found):
        for livery in found:
            self.add_card(livery, external=False)
        self.merge_installed()
        self.apply_filters()

    def add_card(self, livery, external):
        card = LiveryCard(livery, external)
        card.action.connect(self.card_action)
        card.cancel.connect(self.card_cancel)
        self.cards.append(card)
        self.grid.addWidget(card)
        self.load_image(card)
        return card

    def load_image(self, card):
        livery = card.livery
        if livery.image_path and Path(livery.image_path).is_file():
            card.set_image(QPixmap(str(livery.image_path)))
            return
        if not livery.image_url:
            return
        cached = cache_dir("images") / (livery.reg + Path(livery.image_url).suffix)
        if cached.is_file():
            card.set_image(QPixmap(str(cached)))
            return

        def save(data):
            cached.write_bytes(data)
            if card in self.cards:
                card.set_image(QPixmap(str(cached)))

        Fetch(self.network, livery.image_url, self).done.connect(save)

    def merge_installed(self):
        livery_dir = self.files.livery_dir
        installed = {livery.folder.lower(): livery for livery in liveries.scan_installed(livery_dir)}
        known_regs = {card.livery.reg.lower() for card in self.cards if not card.external}
        for card in list(self.cards):
            if card.external and card.livery.folder.lower() not in installed and not card.busy:
                self.cards.remove(card)
                self.grid.removeWidget(card)
                card.deleteLater()
        shown = {card.livery.folder.lower() for card in self.cards}
        for folder, livery in installed.items():
            if folder not in shown and livery.reg.lower() not in known_regs:
                self.add_card(livery, external=True)
        for card in self.cards:
            folder = card.livery.folder
            card.set_installed(liveries.is_installed(livery_dir, folder), liveries.installed_variant(livery_dir, folder))
        self.apply_filters()

    def apply_filters(self):
        query = self.search.text().strip()
        status = self.status_filter.currentIndex()
        visible = 0
        for card in self.cards:
            show = card.matches(query, status) or card.busy
            card.setVisible(show)
            visible += show
        installed = sum(card.installed for card in self.cards)
        self.count.setText(f"{visible} of {len(self.cards)} liveries, {installed} installed")
        if self.cards and not visible:
            self.show_empty("No livery matches this filter")
        elif self.cards:
            self.show_empty("")
        self.grid.invalidate()

    def simulator_blocks(self):
        if simulator_running():
            QMessageBox.information(self, "Simulator open", "Close MSFS 2024 before you install or remove liveries: it only reads the Maddog package when it starts.")
            return True
        return False

    def card_action(self, card):
        if card.busy or self.simulator_blocks():
            return
        if card.installed:
            answer = QMessageBox.question(self, "Remove livery", f"Remove {card.livery.name} ({card.livery.reg})?")
            if answer == QMessageBox.StandardButton.Yes:
                self.uninstall(card)
            return
        if card.livery.zip_path:
            self.install(card, Path(card.livery.zip_path), delete_after=False)
        else:
            self.download(card)

    def download(self, card):
        livery = card.livery
        target = cache_dir("downloads") / f"{livery.reg}.zip"
        card.set_busy("Downloading…", can_cancel=True)
        transfer = FileDownload(self.network, livery.zip_url, target, parent=self)
        self.downloads[livery.reg] = transfer
        transfer.progress.connect(card.set_progress)
        transfer.done.connect(lambda path: self.downloaded(card, Path(path)))
        transfer.failed.connect(lambda error: self.failed(card, f"Could not download {livery.reg}: {error}"))
        transfer.canceled.connect(lambda: self.canceled(card))
        self.apply_filters()

    def downloaded(self, card, path):
        self.downloads.pop(card.livery.reg, None)
        if card in self.cards:
            self.install(card, path, delete_after=True)
        else:
            path.unlink(missing_ok=True)

    def card_cancel(self, card):
        transfer = self.downloads.get(card.livery.reg)
        if transfer:
            transfer.cancel()

    def canceled(self, card):
        self.downloads.pop(card.livery.reg, None)
        card.set_idle()
        self.message.emit(f"Download of {card.livery.reg} canceled.")

    def failed(self, card, text):
        self.downloads.pop(card.livery.reg, None)
        card.set_idle()
        self.message.emit(text)
        QMessageBox.warning(self, "Livery", text)

    def install(self, card, zip_path, delete_after):
        livery = card.livery
        card.set_busy("Installing…")

        def work(progress):
            try:
                liveries.install(zip_path, livery, self.files, progress)
            finally:
                if delete_after:
                    zip_path.unlink(missing_ok=True)

        self.tasks.start(
            work,
            on_progress=card.set_progress,
            on_done=lambda _: self.changed(card, f"{livery.name} installed."),
            on_failed=lambda error: self.failed(card, f"Could not install {livery.reg}: {error}"),
        )

    def uninstall(self, card):
        livery = card.livery
        card.set_busy("Removing…")
        self.tasks.start(
            lambda progress: liveries.uninstall(livery, self.files),
            on_done=lambda _: self.changed(card, f"{livery.name} removed."),
            on_failed=lambda error: self.failed(card, f"Could not remove {livery.reg}: {error}"),
        )

    def changed(self, card, text):
        card.set_idle()
        self.merge_installed()
        self.message.emit(text)

    def reset_paint_kit(self):
        self.paint_kit = None
        target = downloads_dir() / Path(PAINT_KIT_URL).name
        self.kit_progress.hide()
        self.kit_pause.hide()
        self.kit_cancel.hide()
        self.kit_show.setVisible(target.is_file())
        self.kit_button.show()
        partial = target.with_name(target.name + ".part")
        self.kit_button.setText("Resume paint kit" if partial.is_file() else "Download paint kit")

    def paint_kit_clicked(self):
        target = downloads_dir() / Path(PAINT_KIT_URL).name
        partial = target.with_name(target.name + ".part")
        resume = False
        if partial.is_file():
            box = QMessageBox(QMessageBox.Icon.Question, "Paint kit", "There is an unfinished download of the paint kit.", parent=self)
            keep = box.addButton("Resume", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Start over", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() == box.button(QMessageBox.StandardButton.Cancel):
                return
            resume = box.clickedButton() == keep
        elif target.is_file():
            answer = QMessageBox.question(self, "Paint kit", f"{target.name} is already in {target.parent}. Download it again?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.paint_kit = FileDownload(self.network, PAINT_KIT_URL, target, resume=resume, parent=self)
        self.paint_kit.progress.connect(self.paint_kit_progress)
        self.paint_kit.done.connect(lambda path: self.paint_kit_finished(f"Paint kit saved to {path}."))
        self.paint_kit.failed.connect(lambda error: self.paint_kit_finished(f"Could not download the paint kit: {error}"))
        self.paint_kit.canceled.connect(lambda: self.paint_kit_finished("Paint kit download stopped."))
        self.kit_button.hide()
        self.kit_show.hide()
        self.kit_progress.setRange(0, 0)
        self.kit_progress.show()
        self.kit_pause.show()
        self.kit_cancel.show()

    def paint_kit_progress(self, done, total):
        if total > 0:
            self.kit_progress.setRange(0, 1000)
            self.kit_progress.setValue(int(done * 1000 / total))
            self.kit_progress.setFormat(f"Paint kit: {megabytes(done)} of {megabytes(total)}")

    def stop_paint_kit(self, keep_partial):
        if self.paint_kit:
            self.paint_kit.cancel(keep_partial)

    def paint_kit_finished(self, text):
        self.reset_paint_kit()
        self.message.emit(text)

    def busy(self):
        return any(card.busy for card in self.cards) or self.paint_kit is not None
