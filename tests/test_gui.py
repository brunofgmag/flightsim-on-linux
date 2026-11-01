import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

GUI = Path(__file__).resolve().parent.parent / "bin" / "flightsim-on-linux-gui"
FAKE_DBUS_SEND = '#!/bin/sh\necho "$*" >> "$DBUS_LOG"\ncase "$*" in\n  *NameHasOwner*) if [ -e "$CANCELLED" ]; then rm -f "$CANCELLED"; echo "   boolean false"; else echo "   boolean true"; fi ;;\nesac\n'
FAKE_TERMINAL = '#!/bin/sh\necho "$(basename "$0") $1" > "$TERMINAL_LOG.who"\ncase "$1" in -e|-x|--) shift ;; esac\n"$@" < /dev/null > "$TERMINAL_LOG" 2>&1\n'
TASK = "sh -c 'echo first line; sleep 1; echo last line'"
TOOLS = ("bash", "sh", "mktemp", "dirname", "readlink", "rm", "tail", "sleep", "kill", "pkill", "cat", "basename", "printf", "setsid", "cut", "grep", "touch", "true", "false", "seq", "python3", "tee")


def fake_kdialog(yesno, warning="exit 1"):
    return (f'#!/bin/sh\ncase "$*" in\n  *--progressbar*) echo progressbar >> "$KDIALOG_LOG"; echo "org.kde.kdialog-1 /ProgressDialog" ;;\n'
            f'  *--yesno*) echo yesno >> "$KDIALOG_LOG"; echo $$ > "$KDIALOG_PID"; {yesno} ;;\n'
            f'  *--warningyesno*) echo warningyesno >> "$KDIALOG_LOG"; {warning} ;;\nesac\n')


FAKE_CLI = (
    '#!/bin/bash\necho "$*" >> "$CLI_LOG"\ncase "$*" in\n'
    '  "addon list --json") cat "$LIST_JSON" ;;\n'
    '  "addon install "?*" "?*) echo "installed $3 from $4" ;;\n'
    '  "addon install "?*) cat "$WHERE_TO_GET" ;;\n'
    '  *) echo "ran $*" ;;\nesac\n'
)
FAKE_PICKER = (
    '#!/bin/sh\necho "$(basename "$0") $*" >> "$DIALOG_LOG"\ncase "$*" in\n'
    '  *--list*|*--checklist*|*--radiolist*) cat "$ANSWER"; exit "$ANSWER_STATUS" ;;\n'
    '  *--file-selection*|*--getopenfilename*) cat "$PICKED"; exit "$PICKED_STATUS" ;;\nesac\n'
)
STUBS = (
    'CLI="$FAKE_CLI"\n'
    'show_text() { { echo "== $1"; cat "$2"; } >> "$SHOWN"; }\n'
)
STUB_PROGRESS = 'run_with_progress() { echo "progress: $1" >> "$SHOWN"; shift; "$@" > "$LOG" 2>&1; }\n'
FAKE_ZENITY = '#!/bin/sh\ncase "$*" in\n  *--progress*) echo "$*" > "$ZENITY_LOG"; echo "Show output"; cat > /dev/null ;;\nesac\n'


class GuiTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in TOOLS:
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.fake("dbus-send", FAKE_DBUS_SEND)
        self.calls = self.root / "dbus.log"
        self.terminal_log = self.root / "terminal.log"

    def tearDown(self):
        self.temp.cleanup()

    def fake(self, name, body):
        (self.bin / name).write_text(body)
        (self.bin / name).chmod(0o755)

    def run_gui(self, script, dialog="kdialog", desktop="KDE", stdin=None, **extra):
        env = dict(PATH=str(self.bin), HOME=str(self.root), FSOL_DIALOG=dialog, XDG_CURRENT_DESKTOP=desktop,
                   DBUS_LOG=str(self.calls), TERMINAL_LOG=str(self.terminal_log),
                   KDIALOG_PID=str(self.root / "kdialog.pid"), ZENITY_LOG=str(self.root / "zenity.log"),
                   KDIALOG_LOG=str(self.root / "kdialog.log"), CANCELLED=str(self.root / "cancelled"), **extra)
        result = subprocess.run([str(self.bin / "bash"), "-c", f'source "{GUI}"; {script}'], env=env, capture_output=True, text=True, timeout=30, input=stdin)
        return result.returncode, result.stdout

    def terminal_output(self):
        for _ in range(50):
            if self.terminal_log.exists() and "Finished" in self.terminal_log.read_text():
                return self.terminal_log.read_text()
            time.sleep(0.1)
        return self.terminal_log.read_text() if self.terminal_log.exists() else ""


class KdialogProgressTest(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.fake("kdialog", fake_kdialog("exit 1"))

    def dbus_calls(self):
        return self.calls.read_text().splitlines()

    def kdialog_calls(self):
        log = self.root / "kdialog.log"
        return log.read_text().splitlines() if log.exists() else []

    def test_the_dialog_is_closed_with_a_method_call(self):
        self.run_gui('run_with_progress "Testing" true')
        self.assertIn("--session --type=method_call --dest=org.kde.kdialog-1 /ProgressDialog org.kde.kdialog.ProgressDialog.close", self.dbus_calls())

    def test_the_cancel_button_stays(self):
        self.run_gui('run_with_progress "Testing" true')
        self.assertFalse([call for call in self.dbus_calls() if "showCancelButton" in call])

    def test_the_dialog_shows_the_last_line_of_the_output(self):
        self.run_gui(f'run_with_progress "Testing" {TASK}')
        self.assertIn("ProgressDialog.setLabelText string:Testing\nfirst line\n", self.calls.read_text())

    def test_the_command_status_comes_back(self):
        status, _ = self.run_gui('run_with_progress "Testing" false')
        self.assertEqual(status, 1)

    def test_a_task_shorter_than_the_delay_never_asks_about_the_output(self):
        self.run_gui(f'run_with_progress "Testing" {TASK}', FSOL_OFFER_DELAY="5")
        self.assertNotIn("yesno", self.kdialog_calls())

    def test_a_quick_task_never_leaves_an_offer_behind(self):
        self.run_gui('for i in $(seq 1 40); do run_with_progress "Testing" false; done; sleep 0.5', FSOL_OFFER_DELAY="5")
        self.assertNotIn("yesno", self.kdialog_calls())

    def test_cancel_then_stop_ends_the_task_and_its_children_and_says_so(self):
        self.fake("kdialog", fake_kdialog("exit 1", warning="exit 0"))
        (self.root / "cancelled").touch()
        started = time.monotonic()
        status, out = self.run_gui(f"run_with_progress Testing sh -c 'sleep 30 & echo $! > {self.root}/child; wait'; cat \"$LOG\"")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("Stopped before it finished.", out)
        child = int((self.root / "child").read_text())
        time.sleep(0.2)
        self.assertFalse(Path(f"/proc/{child}").exists())

    def test_cancel_then_keep_going_opens_the_progress_again_and_lets_the_task_finish(self):
        (self.root / "cancelled").touch()
        status, out = self.run_gui(f'run_with_progress "Testing" {TASK}; echo "status $?"; cat "$LOG"')
        self.assertEqual(self.kdialog_calls().count("progressbar"), 2)
        self.assertIn("warningyesno", self.kdialog_calls())
        self.assertIn("status 0", out)
        self.assertIn("last line", out)
        self.assertNotIn("Stopped", out)


class ShowOutputTest(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.fake("konsole", FAKE_TERMINAL)

    def test_kdialog_offers_the_output_and_the_terminal_follows_the_task_to_its_end(self):
        self.fake("kdialog", fake_kdialog("exit 0"))
        self.run_gui(f'run_with_progress "Testing" {TASK}', FSOL_OFFER_DELAY="0")
        output = self.terminal_output()
        self.assertIn("first line", output)
        self.assertIn("last line", output)
        self.assertIn("Finished", output)

    def test_an_offer_nobody_answers_closes_when_the_task_ends(self):
        self.fake("kdialog", fake_kdialog("exec sleep 60"))
        started = time.monotonic()
        self.run_gui(f'run_with_progress "Testing" {TASK}', FSOL_OFFER_DELAY="0")
        self.assertLess(time.monotonic() - started, 10)
        pid = int((self.root / "kdialog.pid").read_text())
        time.sleep(0.2)
        self.assertFalse(Path(f"/proc/{pid}").exists())
        self.assertFalse(self.terminal_log.exists())

    def test_zenity_shows_the_button_in_the_progress_and_opens_the_terminal(self):
        self.fake("zenity", FAKE_ZENITY)
        self.run_gui(f'run_with_progress "Testing" {TASK}', dialog="zenity")
        self.assertIn("--extra-button=Show output", (self.root / "zenity.log").read_text())
        self.assertIn("last line", self.terminal_output())


class ZenityCancelTest(GuiTestCase):
    def test_cancel_then_stop_ends_the_task_and_says_so(self):
        self.fake("zenity", '#!/bin/sh\ncase "$*" in\n  *--progress*) exit 1 ;;\n  *--question*) echo question >> "$ZENITY_LOG"; exit 0 ;;\nesac\n')
        started = time.monotonic()
        status, out = self.run_gui('run_with_progress Testing sleep 30; echo "status $?"; cat "$LOG"', dialog="zenity")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("question", (self.root / "zenity.log").read_text())
        self.assertIn("Stopped before it finished.", out)
        self.assertNotIn("status 0", out)


class TerminalChoiceTest(GuiTestCase):
    def chosen(self, *terminals, desktop="KDE", **extra):
        for name in terminals:
            self.fake(name, "#!/bin/sh\n")
        status, out = self.run_gui("terminal_command", desktop=desktop, **extra)
        return out.strip() if status == 0 else None

    def test_kde_prefers_konsole(self):
        self.assertEqual(self.chosen("xdg-terminal-exec", "konsole"), "konsole -e")

    def test_elsewhere_the_desktop_default_comes_first(self):
        self.assertEqual(self.chosen("xdg-terminal-exec", "konsole", desktop="GNOME"), "xdg-terminal-exec")

    def test_gnome_terminal_runs_a_command_after_two_dashes(self):
        self.assertEqual(self.chosen("gnome-terminal", desktop="GNOME"), "gnome-terminal --")

    def test_xfce4_terminal_runs_a_command_after_x(self):
        self.assertEqual(self.chosen("xfce4-terminal", desktop="XFCE"), "xfce4-terminal -x")

    def test_the_terminal_variable_wins(self):
        self.assertEqual(self.chosen("konsole", "xterm", TERMINAL="xterm"), "xterm -e")

    def test_no_terminal_means_none(self):
        self.assertIsNone(self.chosen())


class AddonMenuTest(GuiTestCase):
    WHERE = "A\n  get it from: https://example.org/a\n  kind: installer\n  then run: flightsim-on-linux addon install a <the file you downloaded>\n"

    def setUp(self):
        super().setUp()
        self.addons = self.root / "addons"
        self.addons.mkdir()
        self.rows = []
        self.log = {name: self.root / f"{name}.log" for name in ("cli", "dialogs", "shown")}
        self.fake("fake-cli", FAKE_CLI)
        self.fake("zenity", FAKE_PICKER)
        self.fake("kdialog", FAKE_PICKER)

    def add_on(self, id, status="installed, not fixed", installer=False, menu=None, watcher=None):
        name = id.upper()
        self.rows.append({"id": id, "name": name, "status": status, "can_install": installer, "shortcut": bool(menu), "watcher": bool(watcher)})
        info = {}
        if menu is not None:
            info["shortcuts"] = [{"title": name}]
        if watcher is not None:
            info["watcher"] = {"app": "x"}
        (self.addons / id).mkdir()
        (self.addons / id / "addon.json").write_text(json.dumps(info))

    def flow(self, script, dialog="kdialog", stdin=None, answer="", answer_status=0, picked="", picked_status=0, where=WHERE, progress=True):
        (self.root / "list.json").write_text(json.dumps(self.rows))
        for name, content in (("answer", answer), ("picked", picked), ("where", where)):
            (self.root / name).write_text(content)
        for log in self.log.values():
            log.write_text("")
        return self.run_gui(STUBS + (STUB_PROGRESS if progress else "") + script, dialog=dialog, stdin=stdin, FAKE_CLI=str(self.bin / "fake-cli"),
                            FSOL_ADDONS=str(self.addons), LIST_JSON=str(self.root / "list.json"), WHERE_TO_GET=str(self.root / "where"),
                            CLI_LOG=str(self.log["cli"]), DIALOG_LOG=str(self.log["dialogs"]), SHOWN=str(self.log["shown"]),
                            ANSWER=str(self.root / "answer"), ANSWER_STATUS=str(answer_status),
                            PICKED=str(self.root / "picked"), PICKED_STATUS=str(picked_status))

    def cli_calls(self):
        return [line for line in self.log["cli"].read_text().splitlines() if line != "addon list --json"]

    def dialogs(self):
        return self.log["dialogs"].read_text()

    def shown(self):
        return self.log["shown"].read_text()


class CliContractTest(AddonMenuTest):
    SPEC = {"name": "Demo", "tested_version": "1", "tested_on": "2026-10-31", "needs": [], "touches_addon_files": False,
            "installed": {"community": ["demo-*"]}, "install": {"from": "https://example.org/demo", "paid": None, "kind": "installer"}}

    def real_install_output(self):
        folder = self.addons / "demo"
        folder.mkdir()
        (folder / "addon.json").write_text(json.dumps(self.SPEC))
        for script in ("apply", "check", "undo", "install"):
            (folder / script).write_text("#!/bin/sh\necho ran-$0\n")
            (folder / script).chmod(0o755)
        env = dict(os.environ, FSOL_ADDONS=str(self.addons), HOME=str(self.root), XDG_STATE_HOME=str(self.root / "state"))
        return subprocess.run([str(GUI.parent / "flightsim-on-linux"), "addon", "install", "demo"], env=env, capture_output=True, text=True, timeout=30).stdout

    def test_the_cli_asks_for_a_file_with_the_line_the_helper_looks_for(self):
        (self.root / "where").write_text(self.real_install_output())
        _, out = self.flow('grep -q "$NEEDS_FILE" "$WHERE_TO_GET"; echo "found $?"')
        self.assertEqual(out.strip(), "found 0")


class MenuEntriesTest(AddonMenuTest):
    NEW = ("Install an add-on", "Add menu shortcuts", "Start add-ons with the simulator")

    def test_zenity_lists_the_new_entries_with_their_keys(self):
        self.flow("choose", dialog="zenity")
        for label, key in zip(self.NEW, ("install", "shortcuts", "watchers")):
            self.assertIn(f" {label} {key} FALSE", self.dialogs())

    def test_kdialog_lists_the_new_entries_with_their_keys(self):
        self.flow("choose")
        for label, key in zip(self.NEW, ("install", "shortcuts", "watchers")):
            self.assertIn(f" {key} {label} off ", self.dialogs())

    def test_the_terminal_numbers_the_new_entries(self):
        for number, key in (("2", "install"), ("3", "shortcuts"), ("4", "watchers"), ("5", "check"), ("6", "build"), ("7", "doctor")):
            _, out = self.flow("choose 2> /dev/null", dialog="terminal", stdin=f"{number}\n")
            self.assertEqual(out.strip(), key)

    def test_the_terminal_names_the_new_entries(self):
        _, out = self.flow("choose 2>&1 > /dev/null", dialog="terminal", stdin="1\n")
        for label in self.NEW:
            self.assertIn(label, out)

    def test_main_runs_each_new_entry(self):
        script = ('choose() { local n; n="$(cat "$SHOWN.count" 2> /dev/null || echo 0)"; echo $((n + 1)) > "$SHOWN.count"; case $((n + 1)) in 1) echo install ;; 2) echo shortcuts ;; 3) echo watchers ;; esac; }\n'
                  'install_addon() { echo ran-install; }; add_shortcuts() { echo ran-shortcuts; }; switch_watchers() { echo ran-watchers; }\nmain')
        _, out = self.flow(script)
        self.assertEqual([line for line in out.splitlines() if line.startswith("ran-")], ["ran-install", "ran-shortcuts", "ran-watchers"])


class InstallAddonTest(AddonMenuTest):
    def setUp(self):
        super().setUp()
        self.add_on("a", installer=True)
        self.add_on("b", installer=False)

    def test_only_add_ons_with_an_installer_script_are_offered(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("install_addon", dialog=dialog)
            self.assertIn("A", self.dialogs())
            self.assertNotIn("B", self.dialogs())

    def test_it_is_a_single_choice_with_the_first_add_on_picked(self):
        self.flow("install_addon", dialog="zenity")
        self.assertIn("--radiolist", self.dialogs())
        self.assertIn("TRUE A installed, not fixed a", self.dialogs())
        self.flow("install_addon")
        self.assertIn("--radiolist", self.dialogs())
        self.assertIn("a A: installed, not fixed on", self.dialogs())
        self.assertNotIn("--separate-output", self.dialogs())

    def test_it_says_where_to_get_it_asks_for_the_file_and_runs_the_install_with_it(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("install_addon", dialog=dialog, answer="a\n", picked="/home/x/Setup.exe\n")
            self.assertEqual(self.cli_calls(), ["addon install a", "addon install a /home/x/Setup.exe"])
            shown = self.shown()
            self.assertLess(shown.index("progress: Getting A ready"), shown.index("get it from: https://example.org/a"))
            self.assertLess(shown.index("get it from: https://example.org/a"), shown.index("progress: Installing A"))
            self.assertIn("installed a from /home/x/Setup.exe", shown)

    def test_the_file_picker_opens_after_the_text_with_the_place_to_get_it(self):
        self.flow("install_addon", dialog="zenity", answer="a\n", picked="/home/x/Setup.exe\n")
        self.assertIn("--file-selection", self.dialogs())
        self.flow("install_addon", answer="a\n", picked="/home/x/Setup.exe\n")
        self.assertIn("--getopenfilename", self.dialogs())

    def test_a_cancelled_file_picker_installs_nothing(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("install_addon", dialog=dialog, answer="a\n", picked="", picked_status=1)
            self.assertEqual(self.cli_calls(), ["addon install a"])
            self.assertNotIn("progress: Installing", self.shown())

    def test_a_cancelled_list_runs_nothing(self):
        self.flow("install_addon", answer="", answer_status=1)
        self.assertEqual(self.cli_calls(), [])

    def test_an_install_that_ran_at_once_asks_for_no_file(self):
        self.flow("install_addon", answer="a\n", picked="/home/x/Setup.exe\n", where="A\n  get it from: the package manager\n  next: flightsim-on-linux addon apply a\n")
        self.assertEqual(self.cli_calls(), ["addon install a"])
        self.assertNotIn("--getopenfilename", self.dialogs())

    def test_without_an_installer_script_it_says_so_and_opens_no_list(self):
        self.rows[0]["can_install"] = False
        self.flow("install_addon", answer="a\n")
        self.assertIn("No add-on in this version has an installer script yet", self.shown())
        self.assertEqual(self.dialogs(), "")

    def test_the_terminal_asks_for_a_number_then_a_path_and_expands_the_home_folder(self):
        _, out = self.flow("install_addon 2>&1", dialog="terminal", stdin="1\n~/Setup.exe\n")
        self.assertEqual(self.cli_calls(), ["addon install a", f"addon install a {self.root}/Setup.exe"])
        self.assertIn(" 1) A", out)
        self.assertNotIn("B  ", out)

    def test_the_terminal_takes_only_the_first_number_it_is_given(self):
        self.add_on("c", installer=True)
        self.flow("install_addon 2>&1", dialog="terminal", stdin="2 1\n/home/x/Setup.exe\n")
        self.assertEqual(self.cli_calls(), ["addon install c", "addon install c /home/x/Setup.exe"])

    def test_the_terminal_goes_back_on_an_empty_answer(self):
        self.flow("install_addon 2>&1", dialog="terminal", stdin="\n")
        self.assertEqual(self.cli_calls(), [])
        self.flow("install_addon 2>&1", dialog="terminal", stdin="1\n\n")
        self.assertEqual(self.cli_calls(), ["addon install a"])

    def test_the_terminal_runs_the_install_through_the_real_progress_runner(self):
        status, _ = self.flow("install_addon 2>&1", dialog="terminal", stdin="1\n/home/x/Setup.exe\n", progress=False)
        self.assertEqual(self.cli_calls(), ["addon install a", "addon install a /home/x/Setup.exe"])
        self.assertIn("installed a from /home/x/Setup.exe", self.shown())


class AddShortcutsTest(AddonMenuTest):
    def setUp(self):
        super().setUp()
        self.add_on("a", menu=False)
        self.add_on("b", menu=True)
        self.add_on("c")
        self.add_on("d", status="not installed", menu=False)

    def test_only_add_ons_with_a_menu_entry_to_write_are_offered(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("add_shortcuts", dialog=dialog)
            for name in ("A", "B", "D"):
                self.assertIn(name, self.dialogs())
            self.assertNotIn("C", self.dialogs().replace("--checklist", ""))

    def test_an_installed_add_on_without_an_entry_is_ticked_and_the_rest_are_not(self):
        self.flow("add_shortcuts", dialog="zenity")
        zenity = self.dialogs()
        self.assertIn("TRUE A installed, not fixed, not in the menu a", zenity)
        self.assertIn("FALSE B installed, not fixed, in the menu b", zenity)
        self.assertIn("FALSE D not installed, not in the menu d", zenity)
        self.flow("add_shortcuts")
        kdialog = self.dialogs()
        self.assertIn("a A: installed, not fixed, not in the menu on", kdialog)
        self.assertIn("b B: installed, not fixed, in the menu off", kdialog)
        self.assertIn("d D: not installed, not in the menu off", kdialog)
        self.assertIn("--separate-output --checklist", kdialog)

    def test_each_chosen_add_on_gets_its_entries(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("add_shortcuts", dialog=dialog, answer="a\nd\n")
            self.assertEqual(self.cli_calls(), ["addon shortcut a", "addon shortcut d"])
            self.assertIn("progress: Adding the menu entries of A", self.shown())
            self.assertIn("ran addon shortcut d", self.shown())

    def test_a_cancelled_list_writes_nothing(self):
        self.flow("add_shortcuts", answer="", answer_status=1)
        self.assertEqual(self.cli_calls(), [])

    def test_without_menu_entries_in_any_add_on_it_says_so(self):
        for row in self.rows:
            (self.addons / row["id"] / "addon.json").write_text("{}")
        self.flow("add_shortcuts", answer="a\n")
        self.assertIn("No add-on in this version has a menu entry yet", self.shown())
        self.assertEqual(self.cli_calls(), [])

    def test_the_terminal_takes_the_numbers_of_the_rows(self):
        _, out = self.flow("add_shortcuts 2>&1", dialog="terminal", stdin="1 3\n")
        self.assertEqual(self.cli_calls(), ["addon shortcut a", "addon shortcut d"])
        self.assertIn(" 3) D", out)
        self.assertNotIn(" 4)", out)

    def test_a_number_outside_the_list_is_ignored(self):
        self.flow("add_shortcuts 2>&1", dialog="terminal", stdin="9 2 x\n")
        self.assertEqual(self.cli_calls(), ["addon shortcut b"])


class SwitchWatchersTest(AddonMenuTest):
    def setUp(self):
        super().setUp()
        self.add_on("a", watcher=False)
        self.add_on("b", watcher=True)
        self.add_on("c", watcher=True)
        self.add_on("d")

    def test_only_add_ons_with_a_watcher_are_offered_and_the_ones_that_are_on_are_ticked(self):
        self.flow("switch_watchers", dialog="zenity")
        zenity = self.dialogs()
        self.assertIn("FALSE A installed, not fixed, does not start with the simulator a", zenity)
        self.assertIn("TRUE B installed, not fixed, starts with the simulator b", zenity)
        self.assertIn("TRUE C installed, not fixed, starts with the simulator c", zenity)
        self.assertNotIn(" D ", zenity)
        self.flow("switch_watchers")
        self.assertIn("a A: installed, not fixed, does not start with the simulator off", self.dialogs())
        self.assertIn("b B: installed, not fixed, starts with the simulator on", self.dialogs())
        self.assertNotIn("d D", self.dialogs())

    def test_what_is_newly_ticked_goes_on_and_what_was_unticked_goes_off(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("switch_watchers", dialog=dialog, answer="a\nb\n")
            self.assertEqual(self.cli_calls(), ["addon watcher a", "addon watcher c --off"])
            self.assertIn("progress: Switching on the watcher of A", self.shown())
            self.assertIn("progress: Switching off the watcher of C", self.shown())

    def test_a_list_left_as_it_was_changes_nothing(self):
        self.flow("switch_watchers", answer="b\nc\n")
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.shown(), "")

    def test_ok_with_everything_unticked_switches_every_watcher_off(self):
        self.flow("switch_watchers", answer="", answer_status=0)
        self.assertEqual(self.cli_calls(), ["addon watcher b --off", "addon watcher c --off"])

    def test_cancel_changes_nothing(self):
        for dialog in ("zenity", "kdialog"):
            self.flow("switch_watchers", dialog=dialog, answer="a\n", answer_status=1)
            self.assertEqual(self.cli_calls(), [])

    def test_without_a_watcher_in_any_add_on_it_says_so(self):
        for row in self.rows:
            (self.addons / row["id"] / "addon.json").write_text("{}")
        self.flow("switch_watchers", answer="a\n")
        self.assertIn("No add-on in this version has a watcher yet", self.shown())
        self.assertEqual(self.cli_calls(), [])

    def test_the_terminal_leaves_everything_on_an_empty_answer(self):
        self.flow("switch_watchers 2>&1", dialog="terminal", stdin="\n")
        self.assertEqual(self.cli_calls(), [])

    def test_the_terminal_takes_the_numbers_that_should_be_on(self):
        _, out = self.flow("switch_watchers 2>&1", dialog="terminal", stdin="1\n")
        self.assertEqual(self.cli_calls(), ["addon watcher a", "addon watcher b --off", "addon watcher c --off"])
        self.assertIn("starts with the simulator", out)

    def test_the_terminal_switches_everything_off_on_none(self):
        self.flow("switch_watchers 2>&1", dialog="terminal", stdin="none\n")
        self.assertEqual(self.cli_calls(), ["addon watcher b --off", "addon watcher c --off"])


class SetUpAddonsTest(AddonMenuTest):
    def setUp(self):
        super().setUp()
        self.add_on("a")
        self.add_on("b", status="not installed")

    def test_the_installed_add_on_that_is_not_fixed_is_ticked(self):
        self.flow("set_up_addons", dialog="zenity")
        self.assertIn("TRUE A installed, not fixed a", self.dialogs())
        self.assertIn("FALSE B not installed b", self.dialogs())

    def test_each_chosen_add_on_is_fixed_and_the_results_come_together(self):
        self.flow("set_up_addons", answer="a\nb\n")
        self.assertEqual(self.cli_calls(), ["addon apply a", "addon apply b"])
        self.assertIn("progress: Setting up A", self.shown())
        self.assertIn("== results\nran addon apply a\n\nran addon apply b\n", self.shown())

    def test_without_fixes_it_says_so(self):
        self.rows.clear()
        self.flow("set_up_addons")
        self.assertIn("No add-on fixes are in this version yet", self.shown())


if __name__ == "__main__":
    unittest.main()
