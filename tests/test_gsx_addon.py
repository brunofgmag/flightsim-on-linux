import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fsol import addons
from tests.support import process_stubs, start_stand_in, stub
from tests.test_machine import FakeMachine, write

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "addons" / "gsx-pro"
PARTS = ADDON / "parts"
sys.path.insert(0, str(PARTS))

import clr  # noqa: E402
import couatl_entry  # noqa: E402
import qlm_reg  # noqa: E402

FOUR_VIEWS = ("HKEY_CLASSES_ROOT", "HKEY_CLASSES_ROOT\\Wow6432Node", "HKEY_CURRENT_USER\\Software\\Classes", "HKEY_CURRENT_USER\\Software\\Classes\\Wow6432Node")
OPTIONS = "--in-process-gpu --disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
ENGINE = r"C:\Program Files (x86)\Addon Manager\couatl64\couatl64_boot.exe"
WRAPPER = r"C:\flightsim-on-linux\com-environment.exe"
MSCORLIB = "lib/mono/4.5/mscorlib.dll"
SYSTEM = "lib/mono/gac/System/4.0.0.0__b77a5c561934e089/System.dll"
MARKER = "lib/mono/4.5/System.Core.dll"
THROWING = bytes([0x73, 0x01, 0x00, 0x00, 0x0A, 0x7A])
PATCHED_REGISTER = bytes([0x00, 0x00, 0x00, 0x00, 0x17, 0x2A])
RETURN_FALSE = bytes([0x16, 0x2A])
RETURN_TRUE = bytes([0x17, 0x2A])
PROTON_PATCH = "win32u-cross-process-child-windows.patch"
PROTON_MODULE = "lib/wine/x86_64-unix/win32u.so"
MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><assemblyIdentity name="QlmLicenseLib" version="19.1.25349.2" publicKeyToken="9c65e32c060523a1" processorArchitecture="msil"></assemblyIdentity>
<clrClass clsid="{83CB39F5-95D8-487F-A59D-73A4E7646E40}" progid="Soraco.AnalyticsInfo" threadingModel="Both" name="QlmLicenseLib.AnalyticsInfo" runtimeVersion="v4.0.30319"></clrClass>
<clrClass clsid="{71D1F2AF-6B24-4C5A-BFF1-F4D68A6E74BA}" progid="Soraco.QlmLicense" threadingModel="Both" name="QlmLicenseLib.QlmLicense" runtimeVersion="v4.0.30319"></clrClass>
<clrSurrogate clsid="{A1B2C3D4-0000-4000-8000-000000000001}" name="QlmLicenseLib.QlmRecord"></clrSurrogate>
</assembly>
"""
STOCK_EXE_XML = """<?xml version="1.0" encoding="windows-1252"?>
<SimBase.Document Type="Launch" version="1,0">
    <Filename>exe.xml</Filename>
    <!-- kept as it is -->
    <Launch.Addon>
        <Name>Couatl</Name>
        <Disabled>False</Disabled>
        <Path>C:\\Program Files (x86)\\Addon Manager\\couatl64\\couatl64_boot.exe</Path>
    </Launch.Addon>
    <Launch.Addon>
        <Name>Other</Name>
        <Disabled>False</Disabled>
        <Path>C:\\other.exe</Path>
        <CommandLine>-x</CommandLine>
    </Launch.Addon>
</SimBase.Document>
"""


def assembly(types, member_names=()):
    strings = bytearray(b"\0")

    def string(text):
        offset = len(strings)
        strings.extend(text.encode() + b"\0")
        return offset

    module_name = string("fake.dll")
    type_rows, method_names, bodies = [(0, 0, 1)], [], []
    for namespace, name, methods in types:
        type_rows.append((string(name), string(namespace), len(bodies) + 1))
        for method, body in methods:
            method_names.append(string(method))
            bodies.append(body)
    member_rows = [string(name) for name in member_names]
    present = [(0, 1), (2, len(type_rows)), (6, len(bodies))] + ([(0x0A, len(member_rows))] if member_rows else [])
    valid = sum(1 << table for table, _ in present)
    heap = bytes(strings) + b"\0" * (-len(strings) % 4)
    code, offsets = bytearray(), []
    for body in bodies:
        offsets.append(len(code))
        code += bytes([len(body) << 2 | 2]) + body

    def metadata(code_rva):
        stream = struct.pack("<IBBBBQQ", 0, 2, 0, 0, 1, valid, 0) + b"".join(struct.pack("<I", count) for _, count in present)
        stream += struct.pack("<HHHHH", 0, module_name, 0, 0, 0)
        stream += b"".join(struct.pack("<IHHHHH", 0, name, namespace, 0, 1, first) for name, namespace, first in type_rows)
        stream += b"".join(struct.pack("<IHHHHH", code_rva + offsets[index], 0, 0, method_names[index], 0, 1) for index in range(len(bodies)))
        stream += b"".join(struct.pack("<HHH", 0, name, 0) for name in member_rows)
        stream += b"\0" * (-len(stream) % 4)
        version = b"v4.0.30319\0\0"
        stream_start = 16 + len(version) + 4 + 12 + 20
        root = struct.pack("<IHHII", 0x424A5342, 1, 1, 0, len(version)) + version + struct.pack("<HH", 0, 2)
        root += struct.pack("<II", stream_start, len(stream)) + b"#~\0\0"
        root += struct.pack("<II", stream_start + len(stream), len(heap)) + b"#Strings\0\0\0\0"
        return root + stream + heap

    metadata_rva = 0x2000 + 72
    code_rva = metadata_rva + len(metadata(0))
    blob = metadata(code_rva)
    section = struct.pack("<IHHII", 72, 2, 5, metadata_rva, len(blob)) + bytes(72 - 16) + blob + bytes(code)
    dos = bytearray(0x80)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 0xE0, 0x2102)
    optional = bytearray(0xE0)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<II", optional, 96 + 14 * 8, 0x2000, 72)
    section_header = b".text\0\0\0" + struct.pack("<IIIIIIHHI", len(section), 0x2000, len(section), 0x200, 0, 0, 0, 0, 0x60000020)
    headers = bytes(dos) + b"PE\0\0" + coff + bytes(optional) + section_header
    return headers + bytes(0x200 - len(headers)) + section


def registration_assembly():
    return assembly([
        ("System.Runtime.InteropServices", "RegistrationServices", [("RegisterAssembly", THROWING)]),
        ("System", "Other", [("Throw", THROWING)]),
    ])


def process_assembly():
    return assembly([
        ("System.Diagnostics", "Process", [("get_Id", RETURN_FALSE), ("get_Responding", RETURN_FALSE)]),
        ("System.Diagnostics", "ProcessInfo", [("get_Responding", RETURN_FALSE)]),
    ])


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def curl_zip(version="curl-9.9.9_1-win64-mingw"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(f"{version}/bin/curl.exe", b"MZ fake curl")
        bundle.writestr(f"{version}/bin/curl-ca-bundle.crt", b"fake certificates")
        bundle.writestr(f"{version}/bin/trurl.exe", b"MZ trurl")
    return buffer.getvalue()


class AssemblyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, data):
        path = self.root / "fake.dll"
        path.write_bytes(data)
        return path

    def changed_bytes(self, before, after):
        return [index for index in range(len(before)) if before[index] != after[index]]

    def test_the_throwing_register_assembly_becomes_a_method_that_returns_true_and_nothing_else_changes(self):
        original = registration_assembly()
        path = self.file(original)
        self.assertEqual(clr.state_of(path, "register-assembly"), "original")
        self.assertEqual(clr.apply_patch(path, "register-assembly"), "original")
        patched = path.read_bytes()
        self.assertEqual(clr.state_of(path, "register-assembly"), "patched")
        _, start, size = clr.locate(clr.Image(original), clr.SPECS["register-assembly"])
        self.assertEqual(patched[start:start + size], PATCHED_REGISTER)
        self.assertTrue(all(start <= index < start + size for index in self.changed_bytes(original, patched)))
        self.assertEqual(patched.count(PATCHED_REGISTER), 1)
        self.assertEqual(original.count(THROWING), 2)
        self.assertEqual(patched.count(THROWING), 1)

    def test_another_type_with_the_same_throwing_body_is_not_touched(self):
        path = self.file(registration_assembly())
        clr.apply_patch(path, "register-assembly")
        self.assertEqual(path.read_bytes().count(THROWING), 1)

    def test_patching_twice_changes_nothing_the_second_time(self):
        path = self.file(registration_assembly())
        clr.apply_patch(path, "register-assembly")
        once = path.read_bytes()
        self.assertEqual(clr.apply_patch(path, "register-assembly"), "patched")
        self.assertEqual(path.read_bytes(), once)

    def test_process_responding_flips_one_byte_in_the_right_type(self):
        original = process_assembly()
        path = self.file(original)
        clr.apply_patch(path, "process-responding")
        patched = path.read_bytes()
        self.assertEqual(len(self.changed_bytes(original, patched)), 1)
        self.assertEqual(patched.count(RETURN_TRUE), 1)
        self.assertEqual(clr.state_of(path, "process-responding"), "patched")

    def test_a_body_that_is_neither_the_original_nor_the_patched_one_is_refused_untouched(self):
        other = assembly([("System.Diagnostics", "Process", [("get_Responding", bytes([0x17, 0x17, 0x2A]))])])
        path = self.file(other)
        self.assertEqual(clr.state_of(path, "process-responding"), "unknown")
        with self.assertRaises(clr.ClrError):
            clr.apply_patch(path, "process-responding")
        self.assertEqual(path.read_bytes(), other)

    def test_a_file_that_is_not_a_dotnet_assembly_is_refused(self):
        path = self.file(b"MZ" + bytes(500))
        with self.assertRaises(clr.ClrError):
            clr.state_of(path, "process-responding")

    def test_a_missing_type_is_reported_by_name(self):
        path = self.file(registration_assembly())
        with self.assertRaises(clr.ClrError) as caught:
            clr.state_of(path, "process-responding")
        self.assertIn("System.Diagnostics.Process", str(caught.exception))

    def test_the_command_line_prints_the_state_and_fails_on_a_refusal(self):
        path = self.file(process_assembly())
        run = lambda *args: subprocess.run([sys.executable, "-I", str(PARTS / "clr.py"), *args], capture_output=True, text=True)
        self.assertEqual(run("state", str(path), "process-responding").stdout.strip(), "original")
        self.assertEqual(run("apply", str(path), "process-responding").stdout.strip(), "original")
        self.assertEqual(run("state", str(path), "process-responding").stdout.strip(), "patched")
        self.assertEqual(run("state", str(path), "nonsense").returncode, 2)


MONO_SOURCES = (
    Path.home() / ".local/share/Steam/compatibilitytools.d/Proton-FS2024/files/share/wine/mono/wine-mono",
)


class RealMonoTest(unittest.TestCase):
    def setUp(self):
        source = next((folder for folder in MONO_SOURCES if (folder / MSCORLIB).is_file() and (folder / SYSTEM).is_file()), None)
        if source is None:
            self.skipTest("no wine-mono folder in the Proton FS2024 of this machine")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = source

    def tearDown(self):
        self.temp.cleanup()

    def copy(self, relative):
        target = self.root / Path(relative).name
        shutil.copy(self.source / relative, target)
        return target

    def test_register_assembly_in_a_copy_of_the_real_mscorlib_returns_true_after_the_patch(self):
        path = self.copy(MSCORLIB)
        before = path.read_bytes()
        if clr.state_of(path, "register-assembly") != "original":
            self.skipTest("the wine-mono of this machine is already patched")
        clr.apply_patch(path, "register-assembly")
        after = path.read_bytes()
        changed = [index for index in range(len(before)) if before[index] != after[index]]
        _, start, size = clr.locate(clr.Image(before), clr.SPECS["register-assembly"])
        self.assertEqual(len(before), len(after))
        self.assertEqual(clr.state_of(path, "register-assembly"), "patched")
        self.assertTrue(all(start <= index < start + size for index in changed))
        self.assertEqual(after[start:start + size], PATCHED_REGISTER)
        self.assertEqual(before[start + 5], 0x7A)

    def test_process_responding_in_a_copy_of_the_real_system_dll_changes_exactly_one_byte(self):
        path = self.copy(SYSTEM)
        before = path.read_bytes()
        if clr.state_of(path, "process-responding") != "original":
            self.skipTest("the wine-mono of this machine is already patched")
        clr.apply_patch(path, "process-responding")
        after = path.read_bytes()
        changed = [index for index in range(len(before)) if before[index] != after[index]]
        self.assertEqual(len(changed), 1)
        self.assertEqual((before[changed[0]], after[changed[0]]), (0x16, 0x17))
        self.assertEqual(after[changed[0] + 1], 0x2A)
        self.assertEqual(clr.state_of(path, "process-responding"), "patched")


class RegistrationTextTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manifest = Path(self.temp.name) / "QlmLicenseLib.dll.manifest"
        self.manifest.write_text("\ufeff" + MANIFEST, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_the_install_text_registers_every_class_in_the_four_views(self):
        text = qlm_reg.render(self.manifest)
        self.assertTrue(text.startswith("REGEDIT4\r\n\r\n"))
        for root in FOUR_VIEWS:
            self.assertIn(f"[{root}\\CLSID\\{{83CB39F5-95D8-487F-A59D-73A4E7646E40}}\\InprocServer32]", text)
            self.assertIn(f"[{root}\\Soraco.QlmLicense\\CLSID]", text)
            self.assertIn(f"[{root}\\Record\\{{A1B2C3D4-0000-4000-8000-000000000001}}\\19.1.25349.2]", text)
        self.assertIn('"Assembly"="QlmLicenseLib, Version=19.1.25349.2, Culture=neutral, PublicKeyToken=9c65e32c060523a1"', text)
        self.assertIn('"CodeBase"="file:///C:/Program Files (x86)/Addon Manager/QlmLicenseLib.dll"', text)

    def test_the_text_holds_no_licence_data(self):
        text = qlm_reg.render(self.manifest).lower()
        for word in ("serial", "activation", "licensekey", "password"):
            self.assertNotIn(word, text)

    def test_the_remove_text_deletes_what_the_install_text_wrote(self):
        text = qlm_reg.render(self.manifest, remove=True)
        for root in FOUR_VIEWS:
            self.assertIn(f"[-{root}\\CLSID\\{{71D1F2AF-6B24-4C5A-BFF1-F4D68A6E74BA}}]", text)
            self.assertIn(f"[-{root}\\Soraco.AnalyticsInfo]", text)
            self.assertIn(f"[-{root}\\Record\\{{A1B2C3D4-0000-4000-8000-000000000001}}]", text)
        self.assertEqual(qlm_reg.clsids(self.manifest), ["{83CB39F5-95D8-487F-A59D-73A4E7646E40}", "{71D1F2AF-6B24-4C5A-BFF1-F4D68A6E74BA}"])

    def test_a_manifest_without_an_assembly_identity_is_refused(self):
        self.manifest.write_text('<assembly xmlns="urn:schemas-microsoft-com:asm.v1"></assembly>')
        with self.assertRaises(ValueError):
            qlm_reg.render(self.manifest)


class EntryTest(unittest.TestCase):
    def test_the_stock_entry_is_wrapped_with_the_software_options_and_unwrapped_to_the_same_bytes(self):
        wrapped = couatl_entry.wrap(STOCK_EXE_XML, WRAPPER, OPTIONS)
        self.assertEqual(couatl_entry.state(STOCK_EXE_XML, OPTIONS), "plain")
        self.assertEqual(couatl_entry.state(wrapped, OPTIONS), "wrapped")
        self.assertIn(f"        <Path>{WRAPPER}</Path>\n        <CommandLine>\"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS={OPTIONS}\" \"{ENGINE}\"</CommandLine>\n", wrapped)
        self.assertIn("<!-- kept as it is -->", wrapped)
        self.assertIn("<CommandLine>-x</CommandLine>", wrapped)
        self.assertEqual(couatl_entry.unwrap(wrapped), STOCK_EXE_XML)

    def test_wrapping_twice_changes_nothing(self):
        wrapped = couatl_entry.wrap(STOCK_EXE_XML, WRAPPER, OPTIONS)
        self.assertEqual(couatl_entry.wrap(wrapped, WRAPPER, OPTIONS), wrapped)

    def test_an_entry_with_arguments_keeps_them_after_the_program(self):
        text = STOCK_EXE_XML.replace("couatl64_boot.exe</Path>", "couatl64_boot.exe</Path>\n        <CommandLine>-flag \"a b\"</CommandLine>")
        wrapped = couatl_entry.wrap(text, WRAPPER, OPTIONS)
        self.assertIn(f"\"{ENGINE}\" -flag \"a b\"</CommandLine>", wrapped)
        self.assertEqual(couatl_entry.unwrap(wrapped), text)

    def test_other_options_are_reported_as_other_and_left_alone(self):
        wrapped = couatl_entry.wrap(STOCK_EXE_XML, WRAPPER, "--other")
        self.assertEqual(couatl_entry.state(wrapped, OPTIONS), "other")
        self.assertEqual(couatl_entry.wrap(wrapped, WRAPPER, OPTIONS), wrapped)

    def test_an_exe_xml_without_the_engine_is_absent(self):
        text = STOCK_EXE_XML.replace("couatl64_boot.exe", "somethingelse.exe")
        self.assertEqual(couatl_entry.state(text, OPTIONS), "absent")
        self.assertEqual(couatl_entry.unwrap(text), text)

    def test_a_commented_out_entry_is_not_the_engine(self):
        text = STOCK_EXE_XML.replace("<Launch.Addon>\n        <Name>Couatl", "<!-- <Launch.Addon>\n        <Name>Couatl").replace("</Launch.Addon>\n    <Launch.Addon>\n        <Name>Other", "</Launch.Addon> -->\n    <Launch.Addon>\n        <Name>Other", 1)
        self.assertEqual(couatl_entry.state(text, OPTIONS), "absent")


class GsxCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake = FakeMachine(self.root).build()
        self.found = self.fake.discover()
        self.prefix = self.found.prefix
        self.proton = self.fake.tool
        self.base = self.found.base
        self.record = self.root / "record"
        self.manager = self.prefix / "drive_c/Program Files (x86)/Addon Manager"
        write(self.manager / "QlmLicenseLib.dll.manifest", MANIFEST)
        write(self.manager / "Couatl_Updater2.exe", "MZ")
        write(self.base / "exe.xml", STOCK_EXE_XML)
        source = self.proton / "files/share/wine/mono/wine-mono-11.3.0"
        (source / MSCORLIB).parent.mkdir(parents=True)
        (source / MSCORLIB).write_bytes(registration_assembly())
        (source / SYSTEM).parent.mkdir(parents=True)
        (source / SYSTEM).write_bytes(process_assembly())
        (source / MARKER).write_bytes(b"System.Core of 11.3.0")
        (self.proton / "files/lib/wine/i386-windows").mkdir(parents=True)
        (self.proton / "files/lib/wine/i386-windows/regasm.exe").write_bytes(b"stub32")
        (self.proton / "files/lib/wine/x86_64-windows").mkdir(parents=True)
        (self.proton / "files/lib/wine/x86_64-windows/regasm.exe").write_bytes(b"stub64")
        for folder in ("system32", "syswow64"):
            (self.prefix / "drive_c/windows" / folder).mkdir(parents=True)
        self.zip = self.root / "download/curl-9.9.9_1-win64-mingw.zip"
        self.zip.parent.mkdir()
        self.zip.write_bytes(curl_zip())
        self.shims = self.root / "cache/shims"
        self.shims.mkdir(parents=True)
        (self.shims / "com-environment.exe").write_bytes(b"MZ com-environment")
        os.utime(self.shims / "com-environment.exe", (4102444800, 4102444800))
        self.write_build()
        stub(self.root / "bin/notify-send", "#!/bin/sh\nexit 0\n")
        stub(self.proton / "files/bin/wine", self.wine_stub())
        stub(self.proton / "files/bin/wineserver", "#!/bin/sh\nexit 0\n")
        self.token = str(self.root)
        process_stubs(self.root / "bin", self.token)

    def tearDown(self):
        self.temp.cleanup()

    def wine_stub(self):
        return (
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.record}"\n'
            'if [ "$1" = regedit ]; then\n'
            '  file=$(printf "%s" "$3" | sed "s/^Z://; s/\\\\\\\\/\\//g")\n'
            f'  cp "$file" "{self.record}.reg.$(date +%s%N)"\n'
            "fi\n"
        )

    def write_build(self, patch_listed=True):
        module = self.proton / "files" / PROTON_MODULE
        write(module, "win32u")
        patch = sha256((ROOT / "patches/wine" / PROTON_PATCH).read_bytes())
        lines = ["release cachyos-11.0-20261005-slr x86_64"]
        if patch_listed:
            lines.append(f"patch {patch} {PROTON_PATCH}")
        lines.append(f"module {sha256(module.read_bytes())} {PROTON_MODULE}")
        (self.proton / "fs2024-build.txt").write_text("\n".join(lines) + "\n")

    def environment(self, **extra):
        env = {
            "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "FSOL_PREFIX": str(self.prefix),
            "FSOL_COMPATDATA": str(self.found.compatdata),
            "FSOL_PROTON": str(self.proton),
            "FSOL_BASE": str(self.base),
            "FSOL_COMMUNITY": str(self.found.community),
            "FSOL_CACHE": str(self.root / "cache"),
            "FSOL_CLI": str(self.root / "cli"),
            "FSOL_ADDON": "gsx-pro",
            "FSOL_TEST_TOKEN": self.token,
            "FSOL_SIM_RUNNING": "0",
            "GSX_CURL_URL": self.zip.as_uri(),
            "GSX_CURL_SHA256": sha256(self.zip.read_bytes()),
        }
        env.update(extra)
        return env

    def script(self, name, *args, **extra):
        return subprocess.run([str(ADDON / name), *args], env=self.environment(**extra), capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def recorded(self):
        return self.record.read_text().splitlines() if self.record.exists() else []

    def imported_registries(self):
        return [path.read_bytes().decode("latin-1") for path in sorted(self.root.glob("record.reg.*"))]

    def copy_path(self, relative):
        return self.prefix / "drive_c/windows/mono/mono-2.0" / relative

    def add_registration(self):
        lines = []
        for clsid in qlm_reg.clsids(self.manager / "QlmLicenseLib.dll.manifest"):
            lines += [f"[Software\\\\Classes\\\\CLSID\\\\{clsid}\\\\InprocServer32] 1784762047", '@="mscoree.dll"', ""]
        with (self.prefix / "system.reg").open("a") as target:
            target.write("\n".join(lines) + "\n")

    def run_updater(self):
        runner = start_stand_in(self.root, self.token, "Q:\\Program Files (x86)\\Addon Manager\\Couatl_Updater2.exe")
        self.addCleanup(runner.wait)
        self.addCleanup(runner.kill)

    def snapshot(self):
        return {str(path.relative_to(self.root)): sha256(path.read_bytes()) for path in sorted(self.root.rglob("*")) if path.is_file() and not path.is_symlink() and "record" not in path.name and "cache" not in path.parts}


class ShapeTest(GsxCase):
    def test_the_manifest_is_complete(self):
        addon = addons.load(ADDON)
        self.assertEqual((addon.id, addon.needs, addon.tested_version, addon.tested_on), ("gsx-pro", ["proton-fs2024", "simulator-closed"], "4.0.23", "2026-10-17"))
        self.assertTrue(addon.touches_addon_files)
        self.assertEqual(addon.install, {"from": "http://www.fsdreamteam.com", "kind": "installer"})
        self.assertEqual([shortcut["title"] for shortcut in addon.shortcuts], ["FSDT Installer"])
        self.assertIsNone(addon.watcher)

    def test_there_is_no_install_script_so_the_universal_installer_is_never_run_by_this_tool(self):
        addon = addons.load(ADDON)
        self.assertFalse(addon.can_install)
        self.assertFalse((ADDON / "install").exists())

    def test_no_script_names_the_universal_installer_outside_a_warning(self):
        for path in [ADDON / name for name in ("apply", "undo", "launch", "check")] + sorted(PARTS.glob("*.sh")):
            for number, text in enumerate(path.read_text().splitlines(), 1):
                if "FSDT_Universal_Installer" in text:
                    self.assertTrue(text.strip().startswith("#") or "never" in text, f"{path}:{number}")


class ApplyTest(GsxCase):
    def test_a_missing_manifest_stops_with_the_instruction_to_run_the_universal_installer_once(self):
        (self.manager / "QlmLicenseLib.dll.manifest").unlink()
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("run the FSDT universal installer once", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_apply_prints_the_warnings_before_it_changes_anything(self):
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        first = result.stdout.splitlines()[:2]
        self.assertIn("never runs the universal installer", first[0])
        self.assertIn("RegisterAssembly", first[1])
        self.assertIn("addon undo gsx-pro", first[1])

    def test_apply_registers_the_licence_library_from_its_manifest_through_regedit(self):
        self.script("apply")
        calls = [line for line in self.recorded() if line.startswith("regedit /S Z:")]
        self.assertEqual(len(calls), 1)
        registry = self.imported_registries()
        self.assertEqual(registry[0], qlm_reg.render(self.manager / "QlmLicenseLib.dll.manifest"))

    def test_apply_never_runs_the_universal_installer_or_the_updater(self):
        self.script("apply")
        text = "\n".join(self.recorded()).lower()
        self.assertNotIn("universal", text)
        self.assertNotIn("couatl_updater", text)

    def test_apply_links_regasm_to_the_stubs_of_the_proton(self):
        self.script("apply")
        net = self.prefix / "drive_c/windows/Microsoft.NET"
        self.assertEqual(os.readlink(net / "Framework/v4.0.30319/RegAsm.exe"), str(self.proton / "files/lib/wine/i386-windows/regasm.exe"))
        self.assertEqual(os.readlink(net / "Framework64/v4.0.30319/RegAsm.exe"), str(self.proton / "files/lib/wine/x86_64-windows/regasm.exe"))

    def test_a_regasm_link_that_was_already_there_is_left_alone_and_not_claimed(self):
        folder = self.prefix / "drive_c/windows/Microsoft.NET/Framework64/v4.0.30319"
        folder.mkdir(parents=True)
        (folder / "RegAsm.exe").symlink_to(self.proton / "files/lib/wine/x86_64-windows/regasm.exe")
        self.script("apply")
        self.script("undo")
        self.assertTrue((folder / "RegAsm.exe").is_symlink())
        self.assertFalse((self.prefix / "drive_c/windows/Microsoft.NET/Framework").exists())

    def test_a_registration_that_is_already_in_the_registry_is_not_imported_again(self):
        self.add_registration()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already QlmLicenseLib registered as COM", result.stdout)
        self.assertEqual([line for line in self.recorded() if line.startswith("regedit")], [])
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup/gsx-pro/qlm-remove.reg").exists())

    def test_undo_does_not_remove_a_registration_that_apply_found_in_the_registry(self):
        self.add_registration()
        self.script("apply")
        calls = len(self.recorded())
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already no QlmLicenseLib registration of ours", result.stdout)
        self.assertEqual([line for line in self.recorded()[calls:] if line.startswith("regedit")], [])

    def test_a_second_apply_says_curl_is_already_there(self):
        self.script("apply")
        result = self.script("apply")
        self.assertIn("already curl.exe and curl-ca-bundle.crt", result.stdout)

    def test_a_real_regasm_already_there_is_left_alone(self):
        folder = self.prefix / "drive_c/windows/Microsoft.NET/Framework64/v4.0.30319"
        write(folder / "RegAsm.exe", "the real one")
        result = self.script("apply")
        self.assertEqual((folder / "RegAsm.exe").read_text(), "the real one")
        self.assertIn("already a RegAsm.exe in Framework64", result.stdout)

    def test_apply_copies_wine_mono_and_patches_the_two_methods_in_the_copy_only(self):
        proton_mscorlib = (self.proton / "files/share/wine/mono/wine-mono-11.3.0" / MSCORLIB).read_bytes()
        self.script("apply")
        self.assertEqual(clr.state_of(self.copy_path(MSCORLIB), "register-assembly"), "patched")
        self.assertEqual(clr.state_of(self.copy_path(SYSTEM), "process-responding"), "patched")
        self.assertEqual((self.proton / "files/share/wine/mono/wine-mono-11.3.0" / MSCORLIB).read_bytes(), proton_mscorlib)
        self.assertEqual(clr.state_of(self.proton / "files/share/wine/mono/wine-mono-11.3.0" / MSCORLIB, "register-assembly"), "original")

    def test_apply_keeps_the_originals_it_replaces_under_the_backup_folder(self):
        originals = {name: (self.proton / "files/share/wine/mono/wine-mono-11.3.0" / name).read_bytes() for name in (MSCORLIB, SYSTEM)}
        self.script("apply")
        backup = self.found.compatdata / "flightsim-on-linux-backup/gsx-pro"
        self.assertEqual((backup / "mono" / MSCORLIB).read_bytes(), originals[MSCORLIB])
        self.assertEqual((backup / "mono" / SYSTEM).read_bytes(), originals[SYSTEM])
        self.assertEqual((backup / "exe.xml").read_text(), STOCK_EXE_XML)
        self.assertTrue((backup / "qlm-remove.reg").is_file())

    def test_a_second_apply_keeps_the_first_backup_and_changes_nothing_else(self):
        self.script("apply")
        before = self.snapshot()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("already a wine-mono copy in the prefix", result.stdout)

    def test_apply_installs_curl_with_its_certificates_where_the_32_bit_updater_looks(self):
        self.script("apply")
        for folder in ("system32", "syswow64"):
            self.assertEqual((self.prefix / "drive_c/windows" / folder / "curl.exe").read_bytes(), b"MZ fake curl")
            self.assertEqual((self.prefix / "drive_c/windows" / folder / "curl-ca-bundle.crt").read_bytes(), b"fake certificates")

    def test_a_download_that_does_not_match_the_pinned_hash_is_refused(self):
        result = self.script("apply", GSX_CURL_SHA256="0" * 64)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not match the sha256", result.stderr)
        self.assertFalse((self.prefix / "drive_c/windows/system32/curl.exe").exists())

    def test_the_default_pin_is_curl_8_22_0_build_3_from_curl_se(self):
        text = (PARTS / "common.sh").read_text()
        self.assertIn("https://curl.se/windows/dl-8.22.0_3/curl-8.22.0_3-win64-mingw.zip", text)
        self.assertIn("f38b0ac2e3a280fd414b607c874a21e6dd4d1ed0e2ae622afbf0020917446561", text)

    def test_apply_points_the_engine_entry_at_com_ambiente_and_leaves_the_rest_of_exe_xml(self):
        self.script("apply")
        text = (self.base / "exe.xml").read_text()
        self.assertEqual(couatl_entry.state(text, OPTIONS), "wrapped")
        self.assertIn("<!-- kept as it is -->", text)
        self.assertIn("<Path>C:\\other.exe</Path>", text)
        self.assertEqual((self.prefix / "drive_c/flightsim-on-linux/com-environment.exe").read_bytes(), b"MZ com-environment")

    def test_an_entry_that_already_starts_through_com_ambiente_gets_the_missing_exe_back_without_saying_it_changed_exe_xml(self):
        self.script("apply")
        wrapper = self.prefix / "drive_c/flightsim-on-linux/com-environment.exe"
        wrapper.unlink()
        before = (self.base / "exe.xml").read_bytes()
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(wrapper.read_bytes(), b"MZ com-environment")
        self.assertEqual((self.base / "exe.xml").read_bytes(), before)
        self.assertIn("put com-environment.exe back", result.stdout)
        self.assertNotIn("changed exe.xml", result.stdout)

    def test_an_entry_with_other_options_stays_and_apply_fails_saying_what_to_do(self):
        other = couatl_entry.wrap(STOCK_EXE_XML, WRAPPER, "--other")
        write(self.base / "exe.xml", other)
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("other than", result.stderr)
        self.assertIn("start couatl64_boot.exe directly", result.stderr)
        self.assertNotIn("changed exe.xml", result.stdout)
        self.assertEqual((self.base / "exe.xml").read_text(), other)
        self.assertFalse((self.prefix / "drive_c/flightsim-on-linux").exists())
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup/gsx-pro/exe.xml").exists())

    def test_without_a_gsx_entry_apply_says_to_install_gsx_and_run_it_again(self):
        write(self.base / "exe.xml", STOCK_EXE_XML.replace("couatl64_boot.exe", "somethingelse.exe"))
        result = self.script("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install GSX Pro with the updater", result.stdout)
        self.assertFalse((self.prefix / "drive_c/flightsim-on-linux").exists())

    def test_a_wine_mono_copy_from_an_older_proton_is_replaced_and_patched_again(self):
        old = self.copy_path(MARKER)
        write(old, "System.Core of 11.2.0")
        (old.parent / "mscorlib.dll").write_bytes(registration_assembly())
        self.script("apply")
        self.assertEqual(self.copy_path(MARKER).read_bytes(), b"System.Core of 11.3.0")
        self.assertEqual(clr.state_of(self.copy_path(MSCORLIB), "register-assembly"), "patched")

    def test_an_updater_that_is_running_blocks_apply(self):
        self.run_updater()
        result = self.script("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("close the FSDT updater", result.stderr)


class UndoTest(GsxCase):
    def test_undo_gives_back_every_file_the_apply_changed(self):
        before = self.snapshot()
        self.script("apply")
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.prefix / "drive_c/windows/mono").exists())
        self.assertFalse((self.prefix / "drive_c/windows/Microsoft.NET").exists())
        self.assertFalse((self.prefix / "drive_c/flightsim-on-linux").exists())
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup/gsx-pro").exists())
        self.assertFalse((self.found.compatdata / "flightsim-on-linux-backup/mono-copy").exists())

    def test_undo_removes_the_com_registration_with_the_text_apply_stored(self):
        self.script("apply")
        self.script("undo")
        removal = self.imported_registries()[-1]
        self.assertEqual(removal, qlm_reg.render(self.manager / "QlmLicenseLib.dll.manifest", remove=True))

    def test_undo_restores_the_curl_that_was_there_before(self):
        write(self.prefix / "drive_c/windows/system32/curl.exe", "older curl")
        self.script("apply")
        self.script("undo")
        self.assertEqual((self.prefix / "drive_c/windows/system32/curl.exe").read_text(), "older curl")
        self.assertFalse((self.prefix / "drive_c/windows/syswow64/curl.exe").exists())

    def test_undo_keeps_a_wine_mono_copy_another_addon_still_uses_and_unpatches_ours(self):
        self.script("apply")
        record = self.found.compatdata / "flightsim-on-linux-backup/mono-copy/users"
        (record / "axis-and-ohs").write_text("")
        self.script("undo")
        self.assertTrue(self.copy_path(MSCORLIB).is_file())
        self.assertEqual(clr.state_of(self.copy_path(MSCORLIB), "register-assembly"), "original")
        self.assertEqual(clr.state_of(self.copy_path(SYSTEM), "process-responding"), "original")

    def test_undo_leaves_a_regasm_that_is_not_ours(self):
        folder = self.prefix / "drive_c/windows/Microsoft.NET/Framework64/v4.0.30319"
        write(folder / "RegAsm.exe", "the real one")
        self.script("apply")
        self.script("undo")
        self.assertEqual((folder / "RegAsm.exe").read_text(), "the real one")

    def test_undo_without_apply_changes_nothing_and_succeeds(self):
        before = self.snapshot()
        result = self.script("undo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_undo_says_what_it_leaves_alone(self):
        self.script("apply")
        result = self.script("undo")
        self.assertIn("left alone: the FSDT programs, their licences and serial numbers", result.stdout)


class CheckTest(GsxCase):
    def fixed(self):
        self.script("apply")
        self.add_registration()

    def lines(self, result):
        return [line for line in result.stdout.splitlines() if line.startswith(("ok ", "FAIL "))]

    def test_a_fully_fixed_prefix_passes_every_line(self):
        self.fixed()
        result = self.script("check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(self.lines(result)), 9)
        self.assertTrue(all(line.startswith("ok ") for line in self.lines(result)))

    def test_a_prefix_nothing_was_applied_to_fails_each_fix_by_name(self):
        result = self.script("check")
        self.assertEqual(result.returncode, 1)
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        text = "\n".join(failed)
        for part in ("RegAsm.exe", "QlmLicenseLib classes", "mscorlib.dll", "System.dll", "curl.exe", "starts couatl64_boot.exe directly"):
            self.assertIn(part, text)

    def fails_with(self, mutate, expected):
        self.fixed()
        mutate()
        result = self.script("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        self.assertEqual(len(failed), 1, result.stdout)
        self.assertIn(expected, failed[0])

    def test_a_regasm_that_goes_missing_fails_the_check(self):
        self.fails_with(lambda: (self.prefix / "drive_c/windows/Microsoft.NET/Framework/v4.0.30319/RegAsm.exe").unlink(), "RegAsm.exe is missing in Microsoft.NET/Framework/")

    def test_a_registration_that_is_gone_fails_the_check(self):
        self.fails_with(lambda: (self.prefix / "system.reg").write_text(""), "QlmLicenseLib classes are not registered")

    def test_an_unpatched_register_assembly_fails_the_check(self):
        self.fails_with(lambda: self.copy_path(MSCORLIB).write_bytes(registration_assembly()), "is original")

    def test_an_unpatched_process_responding_fails_the_check(self):
        self.fails_with(lambda: self.copy_path(SYSTEM).write_bytes(process_assembly()), "is original")

    def older_copy(self, version="wine-mono-11.2.0"):
        self.fixed()
        self.copy_path(MARKER).write_bytes(b"System.Core of 11.2.0")
        record = self.found.compatdata / "flightsim-on-linux-backup/mono-copy/created"
        if version:
            record.write_text(version + "\n")
        else:
            record.unlink()
        return self.script("check")

    def test_a_patched_wine_mono_copy_older_than_protons_still_passes_and_names_both_versions(self):
        result = self.older_copy()
        self.assertEqual(result.returncode, 0, result.stdout)
        stale = [line for line in self.lines(result) if "the wine-mono copy in the prefix" in line]
        self.assertEqual(len(stale), 1)
        self.assertTrue(stale[0].startswith("ok "))
        for expected in ("wine-mono-11.2.0", "wine-mono-11.3.0", "addon apply gsx-pro", "addon apply axis-and-ohs"):
            self.assertIn(expected, stale[0])

    def test_an_older_copy_with_no_record_of_its_version_passes_and_says_so(self):
        result = self.older_copy(version=None)
        self.assertEqual(result.returncode, 0, result.stdout)
        stale = [line for line in self.lines(result) if "the wine-mono copy in the prefix" in line]
        self.assertTrue(stale[0].startswith("ok "))
        self.assertIn("is not wine-mono-11.3.0", stale[0])
        self.assertIn("not recorded", stale[0])

    def test_an_older_copy_without_the_patches_fails_the_stale_line_and_the_patch_line(self):
        self.fixed()
        self.copy_path(MARKER).write_bytes(b"System.Core of 11.2.0")
        self.copy_path(MSCORLIB).write_bytes(registration_assembly())
        result = self.script("check")
        self.assertEqual(result.returncode, 1, result.stdout)
        failed = [line for line in self.lines(result) if line.startswith("FAIL ")]
        self.assertEqual(len(failed), 2, result.stdout)
        self.assertTrue(any("lacks the patches" in line for line in failed))
        self.assertTrue(any("mscorlib.dll" in line and "is original" in line for line in failed))

    def test_a_curl_that_is_missing_fails_the_check(self):
        self.fails_with(lambda: (self.prefix / "drive_c/windows/syswow64/curl.exe").unlink(), "syswow64/curl.exe")

    def test_a_proton_without_the_child_window_patch_fails_the_check(self):
        self.fails_with(lambda: self.write_build(patch_listed=False), "win32u-cross-process-child-windows.patch")

    def test_an_exe_xml_that_starts_the_engine_directly_fails_the_check(self):
        self.fails_with(lambda: write(self.base / "exe.xml", STOCK_EXE_XML), "starts couatl64_boot.exe directly")

    def test_a_wrapper_that_is_gone_fails_the_check(self):
        self.fails_with(lambda: (self.prefix / "drive_c/flightsim-on-linux/com-environment.exe").unlink(), "does not exist")

    def test_an_exe_xml_with_other_options_fails_the_check(self):
        self.fails_with(lambda: write(self.base / "exe.xml", couatl_entry.wrap(STOCK_EXE_XML, WRAPPER, "--other")), "other WebView2 options")

    def test_the_check_changes_nothing(self):
        self.fixed()
        before = self.snapshot()
        self.script("check")
        self.assertEqual(self.snapshot(), before)


class LaunchTest(GsxCase):
    def test_it_opens_the_updater_in_the_prefix_with_the_install_arguments(self):
        result = self.script("launch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded(), [r"C:\Program Files (x86)\Addon Manager\Couatl_Updater2.exe /SILENT /INSTALLMODE"])

    def test_it_refuses_while_the_simulator_runs(self):
        result = self.script("launch", FSOL_SIM_RUNNING="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("close the simulator", result.stderr)
        self.assertEqual(self.recorded(), [])

    def test_it_says_what_to_do_when_the_updater_is_not_there(self):
        (self.manager / "Couatl_Updater2.exe").unlink()
        result = self.script("launch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("universal installer once", result.stderr)

    def test_it_does_not_open_a_second_updater(self):
        self.run_updater()
        result = self.script("launch")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.recorded(), [])


if __name__ == "__main__":
    unittest.main()
