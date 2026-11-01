from .ini import IniFile


def registered(files):
    ini = IniFile(files.registration_ini)
    key = ini.get("REGISTRATION", "Key").replace("-", "").strip()
    return len(key) == 16 and key.isalnum() and bool(ini.get("REGISTRATION", "PurchaseCode").strip())
