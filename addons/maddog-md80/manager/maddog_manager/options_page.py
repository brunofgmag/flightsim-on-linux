import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from . import options
from .ini import IniFile
from .locations import child


def printers():
    try:
        result = subprocess.run(["lpstat", "-e"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


class SliderRow(QWidget):
    def __init__(self, option, value, on_commit):
        super().__init__()
        self.option = option
        self.on_commit = on_commit
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(option.minimum, option.maximum)
        self.slider.setValue(max(option.minimum, min(option.maximum, value)))
        self.value = QLabel()
        self.value.setMinimumWidth(36)
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.committed = self.slider.value()
        self.show_value(self.committed)
        self.slider.valueChanged.connect(self.changed)
        self.slider.sliderReleased.connect(self.commit)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value)

    def show_value(self, value):
        self.value.setText(str(value))

    def changed(self, value):
        self.show_value(value)
        if not self.slider.isSliderDown():
            self.commit()

    def commit(self):
        value = self.slider.value()
        if value != self.committed:
            self.committed = value
            self.on_commit(self.option, value)


class OptionsPage(QWidget):
    message = pyqtSignal(str)

    def __init__(self, files):
        super().__init__()
        self.files = files
        self.loading = True
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.content)
        importer = QPushButton("Import settings…")
        importer.clicked.connect(self.import_clicked)
        exporter = QPushButton("Export settings…")
        exporter.clicked.connect(self.export_clicked)
        top = QHBoxLayout()
        top.addWidget(QLabel("The Maddog reads these options when it loads. The app saves each change at once."))
        top.addStretch(1)
        top.addWidget(importer)
        top.addWidget(exporter)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(scroll, 1)
        self.build()
        self.loading = False

    def maddogx(self):
        return IniFile(self.files.maddogx_ini)

    def group(self, title):
        box = QGroupBox(title)
        self.body.addWidget(box)
        return box

    def write(self, option, value):
        if self.loading:
            return
        options.write_option(self.files, self.files.maddogx_ini, option, value)

    def write_raw(self, section, key, value):
        if self.loading:
            return
        ini = self.maddogx()
        ini.set(section, key, value)
        ini.save()

    def slider(self, option, ini):
        return SliderRow(option, option.read(ini), self.write)

    def build(self):
        ini = self.maddogx()
        self.toggles = {}
        simulator = self.group("Simulator")
        grid = QVBoxLayout(simulator)
        for toggle in options.TOGGLES:
            box = QCheckBox(toggle.title)
            box.setChecked(toggle.read(ini))
            box.toggled.connect(lambda checked, toggle=toggle: self.write(toggle, checked))
            self.toggles[toggle.key] = box
            grid.addWidget(box)
        form = QFormLayout()
        grid.addLayout(form)
        self.shades = QComboBox()
        self.shades.addItems(options.WINDOW_SHADES)
        self.shades.setCurrentIndex(max(0, min(2, ini.get_int("SYSTEM", "WindowShades"))))
        self.shades.currentIndexChanged.connect(lambda index: self.write_raw("SYSTEM", "WindowShades", index))
        form.addRow("Window shades", self.shades)
        self.printer = QComboBox()
        refresh = QToolButton()
        refresh.setText("Refresh")
        refresh.clicked.connect(self.load_printers)
        printer_row = QHBoxLayout()
        printer_row.addWidget(self.printer, 1)
        printer_row.addWidget(refresh)
        form.addRow("ACARS printer", printer_row)
        self.load_printers()
        self.printer.currentTextChanged.connect(lambda name: self.write_raw("SYSTEM", "ACARSPrinterName", name))
        self.provider = QComboBox()
        self.provider.addItems(options.DATA_PROVIDERS)
        self.provider.setCurrentIndex(max(0, min(4, ini.get_int("SYSTEM", "WXdata"))))
        self.provider.currentIndexChanged.connect(self.provider_changed)
        form.addRow("Data provider", self.provider)

        lights = self.group("Cockpit lighting")
        form = QFormLayout(lights)
        self.light_rows = {}
        for option in options.LIGHTS:
            row = self.slider(option, ini)
            self.light_rows[option.key] = row
            form.addRow(option.title, row)

        sound = self.group("Sound")
        form = QFormLayout(sound)
        form.addRow(options.BOARDING_MUSIC.title, self.slider(options.BOARDING_MUSIC, ini))
        self.crew = QComboBox()
        sets = options.crew_sets(self.files)
        self.crew.addItems(sets)
        current = ini.get("SYSTEM", "Crew")
        self.crew.setCurrentIndex(sets.index(current) if current in sets else 0)
        self.crew.currentTextChanged.connect(lambda name: self.write_raw("SYSTEM", "Crew", name))
        form.addRow("Crew audio set", self.crew)
        form.addRow(options.CREW_VOLUME.title, self.slider(options.CREW_VOLUME, ini))
        self.voice = QComboBox()
        self.voice.addItems(options.PM_VOICES)
        self.voice.setCurrentIndex(max(0, min(1, ini.get_int("SOUND", "VoicePNF"))))
        self.voice.currentIndexChanged.connect(lambda index: self.write_raw("SOUND", "VoicePNF", index))
        form.addRow("PM callouts voice", self.voice)
        form.addRow(options.PM_VOLUME.title, self.slider(options.PM_VOLUME, ini))
        for option in options.SOUNDS:
            form.addRow(option.title, self.slider(option, ini))

        extra = self.group("Additional options")
        form = QFormLayout(extra)
        common = self.files.common_data
        self.simbrief = self.text_field(IniFile(child(common, "simbrief.ini")).get("SIMBRIEF", "PilotID"),
                                        lambda text: options.write_common(self.files, "simbrief.ini", "SIMBRIEF", "PilotID", text))
        form.addRow("SimBrief: Pilot ID", self.simbrief)
        pushover = IniFile(child(common, "pushover.ini"))
        for label, key, secret in (("Pushover: API token", "Token", True), ("Pushover: user key", "User", True), ("Pushover: devices (optional)", "Device", False)):
            field = self.text_field(pushover.get("PUSHOVER", key),
                                    lambda text, key=key: options.write_common(self.files, "pushover.ini", "PUSHOVER", key, text), secret)
            form.addRow(label, field)
        self.logon_label = QLabel("CPDLC/ACARS: logon code")
        self.logon = self.text_field("", lambda text: options.write_cpdlc(self.files, self.provider.currentIndex(), "LogonCode", text), True)
        form.addRow(self.logon_label, self.logon)
        self.cpdlc_enable = QCheckBox("Turn on CPDLC and ACARS")
        self.cpdlc_enable.toggled.connect(lambda checked: self.cpdlc_write("Enable", checked))
        self.white_list = QCheckBox("Use the automatic whitelist")
        self.white_list.toggled.connect(lambda checked: self.cpdlc_write("AutoWhiteList", checked))
        form.addRow("", self.cpdlc_enable)
        form.addRow("", self.white_list)
        self.load_cpdlc()

        axes = self.group("Axis calibration")
        grid = QGridLayout(axes)
        row = 0
        for title, sliders in options.AXES:
            heading = QLabel(title)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            grid.addWidget(heading, row, 0, 1, 2)
            row += 1
            for option in sliders:
                grid.addWidget(QLabel(option.title), row, 0)
                grid.addWidget(self.slider(option, ini), row, 1)
                row += 1
        grid.setColumnStretch(1, 1)
        self.body.addStretch(1)

    def text_field(self, value, on_save, secret=False):
        field = QLineEdit(value)
        if secret:
            field.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        field.setProperty("saved", value)

        def save():
            if self.loading or field.text() == field.property("saved"):
                return
            field.setProperty("saved", field.text())
            on_save(field.text())
            self.message.emit("Saved.")

        field.editingFinished.connect(save)
        return field

    def load_printers(self):
        current = self.maddogx().get("SYSTEM", "ACARSPrinterName")
        names = ["None", "Pushover", *printers()]
        loading, self.loading = self.loading, True
        self.printer.clear()
        self.printer.addItems(names)
        self.printer.setCurrentText(current if current in names else "Pushover")
        self.loading = loading

    def provider_changed(self, index):
        if self.loading:
            return
        self.write_raw("SYSTEM", "WXdata", index)
        self.load_cpdlc()

    def load_cpdlc(self):
        settings = options.cpdlc_settings(self.files, self.provider.currentIndex())
        loading, self.loading = self.loading, True
        self.logon.setText(settings["logon"])
        self.logon.setProperty("saved", settings["logon"])
        self.logon.setVisible(settings["show_logon"])
        self.logon_label.setVisible(settings["show_logon"])
        self.cpdlc_enable.setChecked(settings["enable"])
        self.white_list.setChecked(settings["white_list"])
        self.loading = loading

    def cpdlc_write(self, key, value):
        if not self.loading:
            options.write_cpdlc(self.files, self.provider.currentIndex(), key, value)

    def reload(self):
        ini = self.maddogx()
        loading, self.loading = self.loading, True
        for toggle in options.TOGGLES:
            self.toggles[toggle.key].setChecked(toggle.read(ini))
        for option in options.LIGHTS:
            row = self.light_rows[option.key]
            row.slider.setValue(option.read(ini))
            row.committed = row.slider.value()
        self.loading = loading

    def import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", str(Path.home()), "Maddog settings (*.mdc)")
        if path:
            options.import_settings(self.files, path)
            options.mark_changed(self.files)
            self.reload()
            self.message.emit(f"Settings imported from {path}.")

    def export_clicked(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", str(Path.home() / "maddog.mdc"), "Maddog settings (*.mdc)")
        if path:
            if not path.lower().endswith(".mdc"):
                path += ".mdc"
            options.export_settings(self.files, path)
            self.message.emit(f"Settings exported to {path}.")
