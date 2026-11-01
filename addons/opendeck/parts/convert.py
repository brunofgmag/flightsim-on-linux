import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

MODELS = {
    "20GBD9901": {"label": "Stream Deck +", "rows": 2, "columns": 4, "encoders": 4},
    "20GAT9902": {"label": "Stream Deck XL", "rows": 4, "columns": 8, "encoders": 0},
    "20GBA9901": {"label": "Stream Deck MK.2", "rows": 3, "columns": 5, "encoders": 0},
}

STARTERPACK = "com.amansprojects.starterpack.sdPlugin"
SWITCH_PROFILE = "com.amansprojects.starterpack.switchprofile"
INPUT_SIMULATION = "com.amansprojects.starterpack.inputsimulation"
MULTI_ACTION = "opendeck.multiaction"

REDLINE = "com.kahikara.opendeck-redline"
PIPEWIRE_APP_VOLUME = "fr.jourdois.pipewire.appvolume"

SYSTEM_VITALS = {
    "CPU": ("cpu", {"barMode": "load"}, None),
    "CPU °C": ("cpu", {"barMode": "temp"}, None),
    "GPU": ("gpu", {"barMode": "load"}, "gpuSelector"),
    "GPU °C": ("gpu", {"barMode": "temp"}, "gpuSelector"),
    "GPU Watt": ("gpu", {"barMode": "power"}, "gpuSelector"),
    "RAM": ("ram", {"ramMode": "used"}, None),
    "VRAM": ("vram", {}, "gpuSelector"),
    "LAN Mbp/s": ("net", {}, "networkInterface"),
}

DISCORD = {
    "com.elgato.discord.mute": "me.amankhanna.oadiscord.togglemute",
    "com.elgato.discord.deafen": "me.amankhanna.oadiscord.toggledeafen",
    "com.elgato.discord.streamtoggle": "me.amankhanna.oadiscord.togglescreenshare",
}

VOLUME_APPLICATIONS = {"Discord.exe": "WEBRTC VoiceEngine"}

NO_LINUX_EQUIVALENT = {
    "com.barraider.shadowplay": "NVIDIA ShadowPlay does not exist on Linux",
    "com.vivremotion.windowscockpit": "Windows Cockpit controls Windows, and no Linux commands were chosen to replace it",
}


FONT_FAMILIES = {
    "": "Liberation Sans",
    "arial": "Liberation Sans",
    "arial black": "Archivo Black",
    "comic sans ms": "Comic Neue",
    "courier": "Courier Prime",
    "courier new": "Courier Prime",
    "georgia": "Tinos",
    "impact": "Anton",
    "microsoft sans serif": "Liberation Serif",
    "times new roman": "Liberation Serif",
    "tahoma": "Open Sans",
    "verdana": "Open Sans",
    "trebuchet ms": "Fira Sans",
}

VIRTUAL_KEYS = {
    8: "Backspace", 9: "Tab", 13: "Return", 19: "Pause", 20: "CapsLock", 27: "Escape", 32: "Space",
    33: "PageUp", 34: "PageDown", 35: "End", 36: "Home", 37: "LeftArrow", 38: "UpArrow", 39: "RightArrow",
    40: "DownArrow", 44: "PrintScr", 45: "Insert", 46: "Delete",
    96: "Numpad0", 97: "Numpad1", 98: "Numpad2", 99: "Numpad3", 100: "Numpad4", 101: "Numpad5",
    102: "Numpad6", 103: "Numpad7", 104: "Numpad8", 105: "Numpad9",
    106: "Multiply", 107: "Add", 109: "Subtract", 110: "Decimal", 111: "Divide",
    144: "Numlock", 145: "ScrollLock",
    173: "VolumeMute", 174: "VolumeDown", 175: "VolumeUp",
    176: "MediaNextTrack", 177: "MediaPrevTrack", 178: "MediaStop", 179: "MediaPlayPause",
    **{112 + i: f"F{i + 1}" for i in range(24)},
}

MODIFIERS = [(2, "Control"), (1, "Shift"), (4, "Alt"), (8, "Meta")]

DEFAULT_STATE = {
    "image": "actionDefaultImage",
    "image_scale": 100,
    "background_colour": "#000000",
    "name": "",
    "text": "",
    "show": True,
    "colour": "#FFFFFF",
    "stroke_colour": "#000000",
    "alignment": "top",
    "family": "Liberation Sans",
    "style": "Regular",
    "size": 11,
    "stroke_size": 3,
    "underline": False,
}

MANIFEST_STATE_KEYS = {
    "Image": "image", "Name": "name", "Title": "text", "ShowTitle": "show", "TitleColor": "colour",
    "TitleAlignment": "alignment", "FontFamily": "family", "FontStyle": "style", "FontSize": "size",
    "FontUnderline": "underline",
}


def read_json(path):
    raw = Path(path).read_bytes()
    if raw[:6] == b"ELGATO":
        raise EncryptedManifest(path)
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError(f"unreadable manifest: {path}")


class EncryptedManifest(Exception):
    pass


def font_size(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 16


def sanitize_name(text, limit=40):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"[./\\:*?\"<>|]", "-", text)
    return text[:limit].strip(" -") or "unnamed"


def convert_icon(plugin_dir, relative):
    base = plugin_dir / relative
    for suffix in (".svg", "@2x.png", ".png"):
        if Path(str(base) + suffix).exists():
            return str(base) + suffix
    return str(base) + ".png"


def styled_state(manifest_state, elgato_state):
    state = dict(manifest_state)
    if "Title" in elgato_state:
        state["text"] = elgato_state["Title"]
    if "ShowTitle" in elgato_state:
        state["show"] = bool(elgato_state["ShowTitle"])
    if elgato_state.get("TitleColor"):
        state["colour"] = elgato_state["TitleColor"]
    if elgato_state.get("TitleAlignment"):
        state["alignment"] = elgato_state["TitleAlignment"]
    if "FontFamily" in elgato_state:
        family = elgato_state["FontFamily"] or ""
        state["family"] = FONT_FAMILIES.get(family.strip().lower(), family)
    if "FontStyle" in elgato_state:
        state["style"] = elgato_state["FontStyle"] or "Regular"
    if "FontSize" in elgato_state:
        state["size"] = font_size(elgato_state["FontSize"])
    if "FontUnderline" in elgato_state:
        state["underline"] = bool(elgato_state["FontUnderline"])
    if "OutlineThickness" in elgato_state:
        state["stroke_size"] = font_size(elgato_state["OutlineThickness"])
    return state


class Catalog:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.actions = {}
        self.actions[MULTI_ACTION] = {
            "name": "Multi Action", "uuid": MULTI_ACTION, "plugin": "opendeck", "tooltip": "Execute multiple actions",
            "icon": "opendeck/multi-action.png", "disable_automatic_states": False, "visible_in_action_list": True,
            "supported_in_multi_actions": False, "property_inspector": "", "controllers": ["Keypad"], "encoder": None,
            "states": [dict(DEFAULT_STATE, image="opendeck/multi-action.png")],
        }
        for plugin_dir in sorted((config_dir / "plugins").glob("*.sdPlugin")):
            self.load_plugin(plugin_dir)

    def relative(self, path):
        path = Path(path)
        if path.is_relative_to(self.config_dir):
            return path.relative_to(self.config_dir).as_posix()
        return path.as_posix()

    def load_plugin(self, plugin_dir):
        manifest = read_json(plugin_dir / "manifest.json")
        override = plugin_dir / "manifest.linux.json"
        if override.exists():
            manifest.update(read_json(override))
        plugin_uuid = plugin_dir.name
        for raw in manifest.get("Actions", []):
            icon = convert_icon(plugin_dir, raw.get("Icon", ""))
            inspector = raw.get("PropertyInspectorPath") or manifest.get("PropertyInspectorPath") or ""
            states = []
            for raw_state in raw.get("States", []):
                state = dict(DEFAULT_STATE)
                for key, target in MANIFEST_STATE_KEYS.items():
                    if key in raw_state:
                        state[target] = raw_state[key]
                state["size"] = font_size(state["size"])
                state["image"] = icon if state["image"] == "actionDefaultImage" else convert_icon(plugin_dir, state["image"])
                state["image"] = self.relative(state["image"])
                state["family"] = FONT_FAMILIES.get(state["family"].strip().lower(), state["family"])
                states.append(state)
            encoder = None
            if raw.get("Encoder") is not None:
                raw_encoder = raw["Encoder"]
                trigger = raw_encoder.get("TriggerDescription") or {}
                encoder = {
                    "icon": self.relative(convert_icon(plugin_dir, raw_encoder["Icon"])) if raw_encoder.get("Icon") else "",
                    "stack_color": raw_encoder.get("StackColor", ""),
                    "trigger_description": {
                        "long_touch": trigger.get("LongTouch", ""), "push": trigger.get("Push", ""),
                        "rotate": trigger.get("Rotate", ""), "touch": trigger.get("Touch", ""),
                    },
                    "background": self.relative(convert_icon(plugin_dir, raw_encoder["background"])) if raw_encoder.get("background") else "",
                    "layout": raw_encoder.get("layout", "$X1"),
                }
            self.actions[raw["UUID"]] = {
                "name": raw.get("Name", ""),
                "uuid": raw["UUID"],
                "plugin": plugin_uuid,
                "tooltip": raw.get("Tooltip", ""),
                "icon": self.relative(icon),
                "disable_automatic_states": raw.get("DisableAutomaticStates", False),
                "visible_in_action_list": raw.get("VisibleInActionsList", True),
                "supported_in_multi_actions": raw.get("SupportedInMultiActions", True),
                "property_inspector": self.relative(plugin_dir / inspector) if inspector else "",
                "controllers": raw.get("Controllers", ["Keypad"]),
                "encoder": encoder,
                "states": states,
            }


class Source:
    def __init__(self, root):
        self.root = root
        self.profiles = {}
        for directory in sorted((root / "ProfilesV3").glob("*.sdProfile")):
            manifest = read_json(directory / "manifest.json")
            self.profiles[directory.name[:-10].lower()] = {"dir": directory, "manifest": manifest}
        self.action_owner = {}
        self.plugins = {}
        for plugin_dir in sorted((root / "Plugins").glob("*.sdPlugin")):
            plugin_uuid = plugin_dir.name[:-9]
            try:
                manifest = read_json(plugin_dir / "manifest.json")
            except EncryptedManifest:
                self.plugins[plugin_uuid] = {"dir": plugin_dir, "encrypted": True}
                continue
            self.plugins[plugin_uuid] = {"dir": plugin_dir, "encrypted": False, "manifest": manifest}
            for action in manifest.get("Actions", []):
                self.action_owner[action["UUID"]] = plugin_uuid

    def owner(self, action):
        plugin = (action.get("Plugin") or {}).get("UUID")
        if plugin and plugin in self.plugins:
            return plugin
        if action["UUID"] in self.action_owner:
            return self.action_owner[action["UUID"]]
        candidates = [uuid for uuid in self.plugins if action["UUID"].startswith(uuid + ".")]
        if candidates:
            return max(candidates, key=len)
        if action["UUID"].startswith("com.elgato.streamdeck."):
            return "builtin"
        return plugin or action["UUID"].rsplit(".", 1)[0]


class Losses:
    def __init__(self):
        self.by_reason = collections.Counter()
        self.by_profile = collections.defaultdict(collections.Counter)
        self.details = collections.defaultdict(collections.Counter)

    def add(self, profile, reason, detail):
        self.by_reason[reason] += 1
        self.by_profile[profile][reason] += 1
        self.details[reason][detail] += 1


def classify(source, devices, skipped):
    included, excluded = [], []
    for uuid, profile in source.profiles.items():
        manifest = profile["manifest"]
        model = manifest["Device"]["Model"]
        name = manifest["Name"]
        if model not in MODELS:
            excluded.append((name, model, "device model is not converted: its size is not known here"))
        elif model not in devices:
            excluded.append((name, model, "no --device given for this model"))
        elif name in skipped:
            excluded.append((name, model, "named in --skip"))
        else:
            included.append(uuid)
    return included, excluded


def page_actions(page_dir):
    manifest_path = page_dir / "manifest.json"
    if not manifest_path.exists():
        return []
    manifest = read_json(manifest_path)
    found = []
    for controller in manifest.get("Controllers", []):
        for key, action in (controller.get("Actions") or {}).items():
            found.append((controller.get("Type", "Keypad"), key, action))
    return found


def iter_nested(action):
    yield action
    for entry in action.get("Actions") or []:
        if "UUID" in entry:
            yield from iter_nested(entry)
        else:
            for child in entry.get("Actions") or []:
                yield from iter_nested(child)


class Layout:
    def __init__(self, source, included, devices):
        self.source = source
        self.pages = {}
        self.parents = {}
        self.ids = {}
        self.profile_name = {}
        used = collections.defaultdict(set)
        for uuid in included:
            profile = source.profiles[uuid]
            manifest = profile["manifest"]
            serial = devices[manifest["Device"]["Model"]]
            base = sanitize_name(manifest["Name"])
            candidate, counter = base, 2
            while candidate.lower() in used[serial]:
                candidate, counter = f"{base} ({counter})", counter + 1
            used[serial].add(candidate.lower())
            pages = [page.lower() for page in manifest["Pages"]["Pages"]]
            self.pages[uuid] = pages
            for index, page in enumerate(pages, 1):
                self.ids[(uuid, page)] = (serial, f"{candidate}/Page {index:02d}")
            folder_titles = {}
            queue = list(pages)
            while queue:
                page = queue.pop(0)
                for _, _, action in page_actions(self.page_dir(uuid, page)):
                    for nested in iter_nested(action):
                        if nested["UUID"] != "com.elgato.streamdeck.profile.openchild":
                            continue
                        child = (nested.get("Settings") or {}).get("ProfileUUID", "").lower()
                        if not child or child in self.parents.get(uuid, {}) or child in pages:
                            continue
                        if not self.page_dir(uuid, child).exists():
                            continue
                        self.parents.setdefault(uuid, {})[child] = page
                        title = ((nested.get("States") or [{}])[0]).get("Title", "")
                        folder_titles[child] = title
                        queue.append(child)
            for index, (child, title) in enumerate(folder_titles.items(), 1):
                label = sanitize_name(title, 30) if title.strip() else "untitled"
                self.ids[(uuid, child)] = (serial, f"{candidate}/Folder {index:02d} {label}")
            self.profile_name[uuid] = candidate

    def page_dir(self, uuid, page):
        return self.source.profiles[uuid]["dir"] / "Profiles" / page.upper()

    def all_pages(self, uuid):
        return [page for (owner, page) in self.ids if owner == uuid]


class Converter:
    def __init__(self, source, layout, catalog, config_dir, losses, vitals):
        self.source = source
        self.vitals = vitals
        self.layout = layout
        self.catalog = catalog
        self.config_dir = config_dir
        self.losses = losses
        self.images_copied = 0
        self.images_missing = 0
        self.plugins_used = collections.Counter()
        self.buttons = collections.Counter()

    def convert_profile(self, uuid):
        manifest = self.source.profiles[uuid]["manifest"]
        device = MODELS[manifest["Device"]["Model"]]
        written = []
        default_page = manifest["Pages"].get("Default", "").lower()
        referenced = set(self.layout.all_pages(uuid))
        for page_dir in sorted((self.source.profiles[uuid]["dir"] / "Profiles").iterdir()):
            page = page_dir.name.lower()
            if page in referenced:
                continue
            leftover = page_actions(page_dir)
            reason = "empty Default page of the Elgato profile" if page == default_page else "folder with no button that opens it"
            for _, _, action in leftover:
                self.losses.add(self.label(uuid), reason, action["UUID"])
        for page in self.layout.all_pages(uuid):
            serial, profile_id = self.layout.ids[(uuid, page)]
            keys = [None] * (device["rows"] * device["columns"])
            sliders = [None] * device["encoders"]
            for controller, key, action in page_actions(self.layout.page_dir(uuid, page)):
                column, row = (int(part) for part in key.split(","))
                if controller == "Encoder":
                    if column >= len(sliders):
                        self.losses.add(self.label(uuid), "position does not exist on the device", key)
                        continue
                    position = column
                else:
                    if column >= device["columns"] or row >= device["rows"]:
                        self.losses.add(self.label(uuid), "position does not exist on the device", key)
                        continue
                    position = row * device["columns"] + column
                context = {"serial": serial, "profile": profile_id, "controller": controller, "position": position}
                instance = self.convert_action(uuid, page, action, context, 0)
                if instance is not None:
                    self.buttons[uuid] += 1
                if controller == "Encoder":
                    sliders[position] = instance
                else:
                    keys[position] = instance
            path = self.config_dir / "profiles" / serial / f"{profile_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"keys": keys, "sliders": sliders, "infobars": []}, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append((serial, profile_id))
        return written

    def label(self, uuid):
        manifest = self.source.profiles[uuid]["manifest"]
        return f"{manifest['Name']} [{MODELS[manifest['Device']['Model']]['label']}]"

    def convert_action(self, uuid, page, action, context, index):
        name = self.label(uuid)
        elgato_uuid = action["UUID"]
        if elgato_uuid in ("com.elgato.streamdeck.dial.stack", "com.elgato.streamdeck.keys.stack"):
            children = [child for entry in action.get("Actions") or [] for child in ([entry] if "UUID" in entry else entry.get("Actions") or [])]
            if not children:
                return None
            current = (action.get("Settings") or {}).get("CurrentIdx", 0)
            current = current if 0 <= current < len(children) else 0
            for skipped, child in enumerate(children):
                if skipped != current:
                    self.losses.add(name, "action stack (Action Wheel or Stack) has no equivalent: only the active action was kept", child["UUID"])
            return self.convert_action(uuid, page, children[current], context, index)
        if elgato_uuid == "com.elgato.streamdeck.multiactions.routine":
            return self.convert_multi_action(uuid, page, action, context, index)
        if elgato_uuid.startswith("com.elgato.streamdeck."):
            return self.convert_builtin(uuid, page, action, context, index)
        owner = self.source.owner(action)
        plugin_info = self.source.plugins.get(owner)
        if owner in NO_LINUX_EQUIVALENT:
            self.losses.add(name, NO_LINUX_EQUIVALENT[owner], elgato_uuid)
            return None
        replacement = self.replacement(action)
        if replacement is not None:
            target_uuid, settings = replacement
            if target_uuid not in self.catalog.actions:
                self.losses.add(name, "native replacement is not mapped or not installed", f"{elgato_uuid} -> {target_uuid}")
                return None
            catalog_action = self.catalog.actions[target_uuid]
            self.plugins_used[catalog_action["plugin"]] += 1
            return self.instance(uuid, page, action, catalog_action, settings, context, index)
        if elgato_uuid not in self.catalog.actions:
            if plugin_info is None:
                self.losses.add(name, "plugin missing from the Windows install", owner)
            elif plugin_info["encrypted"]:
                self.losses.add(name, "Marketplace plugin with an encrypted manifest, which OpenDeck cannot read", owner)
            else:
                self.losses.add(name, "action does not exist in the installed plugin", elgato_uuid)
            return None
        catalog_action = self.catalog.actions[elgato_uuid]
        self.plugins_used[catalog_action["plugin"]] += 1
        return self.instance(uuid, page, action, catalog_action, action.get("Settings") or {}, context, index)

    def replacement(self, action):
        elgato_uuid = action["UUID"]
        settings = action.get("Settings") or {}
        if elgato_uuid == "com.vivremotion.systemvitals.plugin":
            metric, extra, option = SYSTEM_VITALS.get(settings.get("my_title", ""), (None, {}, None))
            chosen = dict(extra)
            if option and self.vitals.get(option):
                chosen[option] = self.vitals[option]
            return (f"{REDLINE}.{metric}" if metric else f"{REDLINE}.?", chosen)
        if elgato_uuid in DISCORD:
            return (DISCORD[elgato_uuid], {})
        if elgato_uuid == "com.elgato.volume-controller.manual-detection":
            application = settings.get("application") or {}
            executable = (application.get("executableFile") or "").replace("\\", "/").rsplit("/", 1)[-1]
            step = int(settings.get("volumeStep") or 5)
            return (PIPEWIRE_APP_VOLUME, {
                "app": VOLUME_APPLICATIONS.get(executable, executable),
                "name": application.get("shortName") or application.get("displayName") or executable,
                "step": min(max(step, 1), 20),
            })
        return None

    def convert_multi_action(self, uuid, page, action, context, index):
        name = self.label(uuid)
        groups = action.get("Actions") or []
        for extra in groups[1:]:
            for child in extra.get("Actions") or []:
                self.losses.add(name, "second group of a multi action has no equivalent", child["UUID"])
        instance = self.instance(uuid, page, action, self.catalog.actions[MULTI_ACTION], {}, context, 0)
        children = []
        for child in (groups[0].get("Actions") or []) if groups else []:
            if child["UUID"] == "com.elgato.streamdeck.multiactions.delay":
                self.losses.add(name, "delay inside a multi action has no equivalent", child["UUID"])
                continue
            converted = self.convert_action(uuid, page, child, context, len(children) + 1)
            if converted is not None:
                children.append(converted)
        if not children:
            self.losses.add(name, "multi action with no action that can be converted", "multiactions.routine")
            return None
        instance["children"] = children
        return instance

    def switch_target(self, target_uuid, target_page):
        serial_id = self.layout.ids.get((target_uuid, target_page))
        if serial_id is None:
            return None
        return {"device": serial_id[0], "profile": serial_id[1]}

    def convert_builtin(self, uuid, page, action, context, index):
        name = self.label(uuid)
        elgato_uuid = action["UUID"]
        settings = action.get("Settings") or {}
        pages = self.layout.pages[uuid]
        target = None
        if elgato_uuid in ("com.elgato.streamdeck.page.next", "com.elgato.streamdeck.page.previous"):
            current = pages.index(page) if page in pages else None
            if current is None:
                self.losses.add(name, "page change inside a folder has no equivalent", elgato_uuid)
                return None
            step = 1 if elgato_uuid.endswith("next") else -1
            target = self.switch_target(uuid, pages[(current + step) % len(pages)])
        elif elgato_uuid == "com.elgato.streamdeck.page.goto":
            page_index = settings.get("PageIndex", 1)
            if 1 <= page_index <= len(pages):
                target = self.switch_target(uuid, pages[page_index - 1])
        elif elgato_uuid == "com.elgato.streamdeck.profile.openchild":
            target = self.switch_target(uuid, settings.get("ProfileUUID", "").lower())
        elif elgato_uuid == "com.elgato.streamdeck.profile.backtoparent":
            parent = self.layout.parents.get(uuid, {}).get(page)
            if parent is not None:
                target = self.switch_target(uuid, parent)
        elif elgato_uuid == "com.elgato.streamdeck.profile.rotate":
            target_uuid = settings.get("ProfileUUID", "").lower()
            if target_uuid in self.layout.pages:
                target_pages = self.layout.pages[target_uuid]
                page_index = settings.get("PageIndex", 1)
                page_index = page_index if 1 <= page_index <= len(target_pages) else 1
                target = self.switch_target(target_uuid, target_pages[page_index - 1])
            else:
                reason = "profile switch to a profile left out of the conversion" if target_uuid in self.source.profiles else "profile switch to a profile that no longer exists"
                self.losses.add(name, reason, target_uuid)
                return None
        elif elgato_uuid == "com.elgato.streamdeck.system.hotkey":
            tokens = self.hotkey_tokens(name, settings.get("Hotkeys", []))
            if tokens is None:
                return None
            return self.starterpack(uuid, page, action, INPUT_SIMULATION, {"down": "[" + ", ".join(tokens) + "]"}, context, index)
        elif elgato_uuid == "com.elgato.streamdeck.system.text":
            text = settings.get("pastedText") or ""
            tokens = [f"Text({json.dumps(text, ensure_ascii=False)})"] if text else []
            if text and settings.get("isSendingEnter"):
                tokens.append("Key(Return, Click)")
            new_settings = {"down": "[" + ", ".join(tokens) + "]"} if tokens else {}
            return self.starterpack(uuid, page, action, INPUT_SIMULATION, new_settings, context, index)
        else:
            self.losses.add(name, "Elgato built-in action has no equivalent in OpenDeck", elgato_uuid)
            return None
        if target is None:
            self.losses.add(name, "navigation button with no destination that can be resolved", elgato_uuid)
            return None
        return self.starterpack(uuid, page, action, SWITCH_PROFILE, target, context, index)

    def hotkey_tokens(self, name, hotkeys):
        tokens = []
        for hotkey in hotkeys:
            code = hotkey.get("VKeyCode", -1)
            if code == -1:
                continue
            key = self.key_token(code)
            if key is None:
                self.losses.add(name, "key has no equivalent in enigo", str(code))
                return None
            held = [modifier for bit, modifier in MODIFIERS if hotkey.get("KeyModifiers", 0) & bit]
            tokens += [f"Key({modifier}, Press)" for modifier in held]
            tokens.append(f"Key({key}, Click)")
            tokens += [f"Key({modifier}, Release)" for modifier in reversed(held)]
        return tokens

    def key_token(self, code):
        if 48 <= code <= 57 or 65 <= code <= 90:
            return f"Unicode('{chr(code).lower()}')"
        return VIRTUAL_KEYS.get(code)

    def starterpack(self, uuid, page, action, starter_uuid, settings, context, index):
        catalog_action = self.catalog.actions[starter_uuid]
        self.plugins_used[catalog_action["plugin"]] += 1
        return self.instance(uuid, page, action, catalog_action, settings, context, index)

    def instance(self, uuid, page, action, catalog_action, settings, context, index):
        elgato_states = action.get("States") or []
        manifest_states = catalog_action["states"] or [dict(DEFAULT_STATE)]
        disk_context = f"{context['controller']}.{context['position']}.{index}"
        image_dir = self.config_dir / "images" / context["serial"] / context["profile"] / disk_context
        states = []
        single_state = catalog_action["plugin"] == "opendeck" or catalog_action["uuid"].startswith("com.amansprojects.")
        count = 1 if single_state else max(len(manifest_states), 1)
        for state_index in range(count):
            elgato_state = elgato_states[state_index] if state_index < len(elgato_states) else {}
            state = styled_state(manifest_states[min(state_index, len(manifest_states) - 1)], elgato_state)
            image = elgato_state.get("Image")
            if image:
                source_image = self.layout.page_dir(uuid, page) / image
                if source_image.exists():
                    image_dir.mkdir(parents=True, exist_ok=True)
                    target_name = f"{state_index}{source_image.suffix.lower()}"
                    shutil.copyfile(source_image, image_dir / target_name)
                    state["image"] = target_name
                    self.images_copied += 1
                else:
                    self.images_missing += 1
            states.append(state)
        current_state = action.get("State", 0) if isinstance(action.get("State"), int) else 0
        return {
            "action": catalog_action,
            "context": disk_context,
            "states": states,
            "current_state": current_state if 0 <= current_state < len(states) else 0,
            "settings": settings,
            "children": None,
        }


def required_plugins(source, included, layout):
    needed = collections.Counter()
    for uuid in included:
        for page in layout.all_pages(uuid):
            for _, _, action in page_actions(layout.page_dir(uuid, page)):
                for nested in iter_nested(action):
                    owner = source.owner(nested)
                    if owner != "builtin":
                        needed[owner] += 1
    return needed


def make_writable(root):
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def install_adapted(adapted_dir, config_dir):
    report = {}
    if adapted_dir is None:
        return report
    for plugin_dir in sorted(adapted_dir.glob("*.sdPlugin")):
        target = config_dir / "plugins" / plugin_dir.name
        if target.exists():
            report[plugin_dir.name[:-9]] = "already installed"
            continue
        shutil.copytree(plugin_dir, target, symlinks=False, copy_function=shutil.copyfile)
        make_writable(target)
        report[plugin_dir.name[:-9]] = "installed from --adapted"
    return report


def install_plugins(source, needed, config_dir):
    report = {}
    for plugin_uuid in sorted(needed):
        info = source.plugins.get(plugin_uuid)
        target = config_dir / "plugins" / f"{plugin_uuid}.sdPlugin"
        if info is None:
            report[plugin_uuid] = "missing from the Windows install"
        elif info["encrypted"]:
            report[plugin_uuid] = "encrypted by the Elgato Marketplace; not installed"
        elif target.exists():
            report[plugin_uuid] = "already installed; left as it is"
        else:
            shutil.copytree(info["dir"].resolve(), target, symlinks=False, copy_function=shutil.copyfile)
            make_writable(target)
            report[plugin_uuid] = "installed"
    return report


def parse_devices(values):
    devices = {}
    for value in values:
        model, _, device_id = value.partition("=")
        if model not in MODELS or not device_id.startswith("sd-") or len(device_id) < 4:
            raise ValueError(f"--device wants MODEL=sd-SERIAL with a model from {', '.join(MODELS)}: {value}")
        devices[model] = device_id
    return devices


def build_parser():
    parser = argparse.ArgumentParser(description="Converts the profiles of the Windows Stream Deck app into OpenDeck profiles. Close OpenDeck first.")
    parser.add_argument("--source", type=Path, required=True, help="the Elgato/StreamDeck folder of a Windows install, the one that holds ProfilesV3 and Plugins")
    parser.add_argument("--config", type=Path, required=True, help="OpenDeck's configuration folder, usually ~/.config/opendeck")
    parser.add_argument("--report", type=Path, required=True, help="where to write the JSON report")
    parser.add_argument("--device", action="append", default=[], metavar="MODEL=sd-SERIAL", help=f"the OpenDeck name of a device for an Elgato model ({', '.join(MODELS)}); once for each device you have")
    parser.add_argument("--skip", action="append", default=[], metavar="NAME", help="an Elgato profile name to leave out; repeat it for more")
    parser.add_argument("--adapted", type=Path, help="a folder of .sdPlugin folders to install first, for plugins you adapted or replaced")
    parser.add_argument("--gpu-selector", help="the value of the Redline plugin's gpuSelector for the System Vitals buttons that show a GPU")
    parser.add_argument("--network-interface", help="the Redline plugin's networkInterface for the System Vitals network button")
    return parser


def write_selected_profiles(included, source, layout, devices, config_dir):
    for model, device_id in devices.items():
        default_uuid = next((uuid for uuid in included if source.profiles[uuid]["manifest"]["Device"]["Model"] == model and source.profiles[uuid]["manifest"]["Name"] == "Default Profile"), None)
        if default_uuid is not None:
            first = layout.ids[(default_uuid, layout.pages[default_uuid][0])][1]
            (config_dir / "profiles" / f"{device_id}.json").write_text(json.dumps({"selected_profile": first}, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        devices = parse_devices(args.device)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not devices:
        print("give at least one --device MODEL=sd-SERIAL", file=sys.stderr)
        return 2
    source = Source(args.source)
    included, excluded = classify(source, devices, set(args.skip))
    layout = Layout(source, included, devices)
    needed = required_plugins(source, included, layout)
    adapted_report = install_adapted(args.adapted, args.config)
    plugin_report = install_plugins(source, needed, args.config)
    plugin_report.update(adapted_report)
    catalog = Catalog(args.config)
    losses = Losses()
    vitals = {"gpuSelector": args.gpu_selector, "networkInterface": args.network_interface}
    converter = Converter(source, layout, catalog, args.config, losses, vitals)

    per_device = collections.defaultdict(list)
    for uuid in included:
        manifest = source.profiles[uuid]["manifest"]
        written = converter.convert_profile(uuid)
        per_device[MODELS[manifest["Device"]["Model"]]["label"]].append({
            "elgato": manifest["Name"],
            "opendeck": layout.profile_name[uuid],
            "profiles": len(written),
            "buttons": converter.buttons[uuid],
        })
    write_selected_profiles(included, source, layout, devices, args.config)

    report = {
        "devices": per_device,
        "excluded": [{"elgato": name, "model": model, "reason": reason} for name, model, reason in excluded],
        "plugins_needed": dict(needed),
        "plugins_install": plugin_report,
        "plugins_used_in_output": dict(converter.plugins_used),
        "losses_by_reason": dict(losses.by_reason),
        "losses_by_profile": {name: dict(counter) for name, counter in losses.by_profile.items()},
        "losses_details": {reason: dict(counter) for reason, counter in losses.details.items()},
        "images_copied": converter.images_copied,
        "images_missing": converter.images_missing,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("plugins_install", "losses_by_reason", "images_copied", "images_missing")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
