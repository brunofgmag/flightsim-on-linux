LAYER_SWITCH = "WINE_WAYLAND_STEAM_OVERLAY_LAYER"
LAYER_OFF = "DISABLE_WINE_WAYLAND_STEAM_OVERLAY_LAYER"
STEAM_OVERLAY_RENDERER = "gameoverlayrenderer.so"


def nonzero(value):
    return len(value) > 0 and value != "0"


def steam_injected_its_overlay(env):
    return STEAM_OVERLAY_RENDERER in env.get("LD_PRELOAD", "")


def layer_search_variable(env):
    if "VK_IMPLICIT_LAYER_PATH" in env:
        return "VK_IMPLICIT_LAYER_PATH"
    return "VK_ADD_IMPLICIT_LAYER_PATH"


def add_search_folder(env, variable, folder):
    folders = [entry for entry in env.get(variable, "").split(":") if entry]
    if folder not in folders:
        folders.append(folder)
    env[variable] = ":".join(folders)


def enable(env, manifest_folder):
    if nonzero(env.get(LAYER_OFF, "0")) or not steam_injected_its_overlay(env):
        return False
    env[LAYER_SWITCH] = "1"
    add_search_folder(env, layer_search_variable(env), manifest_folder)
    return True
