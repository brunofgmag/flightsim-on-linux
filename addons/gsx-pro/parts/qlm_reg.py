import sys
import xml.etree.ElementTree as ET

CODEBASE = "file:///C:/Program Files (x86)/Addon Manager/QlmLicenseLib.dll"
MANAGED_CATEGORY = "{62C8FE65-4EBB-45E7-B440-6E39B2CDBF29}"
NAMESPACE = "{urn:schemas-microsoft-com:asm.v1}"
ROOTS = (
    "HKEY_CLASSES_ROOT",
    "HKEY_CLASSES_ROOT\\Wow6432Node",
    "HKEY_CURRENT_USER\\Software\\Classes",
    "HKEY_CURRENT_USER\\Software\\Classes\\Wow6432Node",
)


def read(manifest):
    root = ET.parse(manifest).getroot()
    identity = root.find(f"{NAMESPACE}assemblyIdentity")
    if identity is None:
        raise ValueError(f"{manifest} has no assemblyIdentity")
    classes = [(item.get("clsid"), item.get("progid"), item.get("name"), item.get("runtimeVersion") or "v4.0.30319", item.get("threadingModel") or "Both") for item in root.findall(f"{NAMESPACE}clrClass")]
    surrogates = [(item.get("clsid"), item.get("name")) for item in root.findall(f"{NAMESPACE}clrSurrogate")]
    assembly = f"{identity.get('name')}, Version={identity.get('version')}, Culture=neutral, PublicKeyToken={identity.get('publicKeyToken')}"
    return identity.get("version"), assembly, classes, surrogates


def clsids(manifest):
    return [clsid for clsid, *_ in read(manifest)[2]]


def install_lines(root, version, assembly, classes, surrogates):
    lines = []
    for clsid, progid, name, runtime, threading in classes:
        base = f"{root}\\CLSID\\{clsid}"
        server = [f'"Class"="{name}"', f'"Assembly"="{assembly}"', f'"RuntimeVersion"="{runtime}"', f'"CodeBase"="{CODEBASE}"']
        lines += [f"[{base}]", f'@="{name}"', ""]
        lines += [f"[{base}\\InprocServer32]", '@="mscoree.dll"', f'"ThreadingModel"="{threading}"', *server, ""]
        lines += [f"[{base}\\InprocServer32\\{version}]", *server, ""]
        if progid:
            lines += [f"[{base}\\ProgId]", f'@="{progid}"', ""]
        lines += [f"[{base}\\Implemented Categories\\{MANAGED_CATEGORY}]", ""]
        if progid:
            lines += [f"[{root}\\{progid}]", f'@="{name}"', "", f"[{root}\\{progid}\\CLSID]", f'@="{clsid}"', ""]
    for clsid, name in surrogates:
        lines += [f"[{root}\\Record\\{clsid}\\{version}]", f'"Class"="{name}"', f'"Assembly"="{assembly}"', '"RuntimeVersion"="v4.0.30319"', f'"CodeBase"="{CODEBASE}"', ""]
    return lines


def remove_lines(root, classes, surrogates):
    lines = []
    for clsid, progid, *_ in classes:
        lines += [f"[-{root}\\CLSID\\{clsid}]", ""]
        if progid:
            lines += [f"[-{root}\\{progid}]", ""]
    lines += [f"[-{root}\\Record\\{clsid}]" for clsid, _ in surrogates]
    return lines


def render(manifest, remove=False):
    version, assembly, classes, surrogates = read(manifest)
    lines = []
    for root in ROOTS:
        lines += remove_lines(root, classes, surrogates) if remove else install_lines(root, version, assembly, classes, surrogates)
    return "\r\n".join(["REGEDIT4", "", *lines])


def main(argv):
    if len(argv) != 3 or argv[0] not in ("install", "remove"):
        print("usage: qlm_reg.py install|remove <QlmLicenseLib.dll.manifest> <out.reg>", file=sys.stderr)
        return 2
    try:
        text = render(argv[1], remove=argv[0] == "remove")
    except (OSError, ET.ParseError, ValueError) as error:
        print(f"qlm_reg.py: {error}", file=sys.stderr)
        return 1
    with open(argv[2], "w", encoding="latin-1", newline="") as target:
        target.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
