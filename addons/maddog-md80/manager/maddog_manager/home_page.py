from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget

from . import options
from .ini import IniFile
from .net import Fetch
from .registration import registered


class HomePage(QWidget):
    message = pyqtSignal(str)

    def __init__(self, files, network):
        super().__init__()
        self.files = files
        layout = QVBoxLayout(self)

        install = QGroupBox("Installation")
        form = QFormLayout(install)
        version, self.build = options.installed_build(files)
        self.version = QLabel(version or "unknown")
        self.update = QLabel("Checking the Leonardo site…")
        form.addRow("Installed version", self.version)
        form.addRow("Updates", self.update)
        form.addRow("Registration", QLabel("registered" if registered(files) else "not registered (register in the Load Manager, with the key icon)"))
        form.addRow("Package", QLabel(str(files.package)))
        layout.addWidget(install)

        panel = QGroupBox("Panel state when the Maddog loads")
        form = QFormLayout(panel)
        self.panel_state = QComboBox()
        self.panel_state.addItems(options.PANEL_STATES)
        self.panel_state.activated.connect(self.panel_state_chosen)
        self.panel_current = QLabel()
        form.addRow("Apply", self.panel_state)
        form.addRow("Last chosen", self.panel_current)
        layout.addWidget(panel)
        self.show_panel_state()

        airac = QGroupBox("Navigraph navdata")
        form = QFormLayout(airac)
        info = options.airac_info(files)
        if info:
            form.addRow("AIRAC cycle", QLabel(info.get("cycle", "")))
            form.addRow("Version", QLabel(info.get("version", "")))
            form.addRow("Valid", QLabel(info.get("valid", "")))
        else:
            form.addRow(QLabel("No Navigraph cycle in the Maddog work folder."))
        layout.addWidget(airac)
        layout.addStretch(1)

        fetch = Fetch(network, options.VERSION_URL, self)
        fetch.done.connect(self.version_loaded)
        fetch.failed.connect(lambda error: self.update.setText(f"could not check ({error})"))

    def version_loaded(self, data):
        latest = options.server_build(data.decode("latin-1"))
        if latest is None or self.build is None:
            self.update.setText("could not check")
        elif latest > self.build:
            self.update.setText(f"the site has build {latest}; update with the installer from your store")
        else:
            self.update.setText(f"up to date (build {latest} on the site)")

    def show_panel_state(self):
        name = IniFile(self.files.manager_ini).get("MANAGER", "PanelStateName")
        if name in options.PANEL_STATE_NAMES:
            name = options.PANEL_STATES[options.PANEL_STATE_NAMES.index(name)]
        self.panel_current.setText(name or "none")
        self.panel_state.setCurrentIndex(0)

    def panel_state_chosen(self, index):
        if index <= 0:
            return
        options.apply_panel_state(self.files, index)
        self.show_panel_state()
        self.message.emit(f"Panel state: {options.PANEL_STATES[index]}. It applies the next time the Maddog loads.")
