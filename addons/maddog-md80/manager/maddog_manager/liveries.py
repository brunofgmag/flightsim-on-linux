import json
import shutil
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .ini import IniFile
from .layout import write_layout
from .locations import child

CATALOG_URL = "https://www.flythemaddog.com/downloads/FS2024_Liveries_20th/liveries.json"
PAINT_KIT_URL = "https://www.flythemaddog.com/downloads/Maddog_20th_paintkit.zip"
VARIANT_NAMES = {0: "MD-82", 1: "MD-83", 2: "MD-88"}
TAG_VARIANTS = {"MD82": 0, "MD83": 1, "MD88": 2}
MAX_UNCOMPRESSED = 1 << 30
PACKAGE_LOCK = threading.Lock()


@dataclass
class Livery:
    name: str
    reg: str
    folder: str
    variant: int = -1
    zip_url: str = ""
    image_url: str = ""
    zip_path: Path | None = None
    image_path: Path | None = None

    @property
    def variant_name(self):
        return VARIANT_NAMES.get(self.variant, "")


def parse_catalog(data):
    entries = json.loads(data.decode("utf-8-sig") if isinstance(data, bytes) else data)
    liveries = []
    for entry in entries:
        image = entry.get("image_path_jpg") or entry.get("image_path_png") or ""
        liveries.append(Livery(
            name=entry.get("name") or entry["reg"],
            reg=entry["reg"],
            folder=entry["livery_folder"],
            variant=int(entry.get("variant", -1)),
            zip_url=entry["zip_path"],
            image_url=image,
        ))
    return liveries


def safe_folder(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or len(path.parts) != 1 or name in (".", ".."):
        raise ValueError(f"invalid livery folder name: {name!r}")
    return name


def installed_variant(livery_dir, folder):
    path = child(Path(livery_dir), folder)
    if not path.is_dir():
        return -1
    cfg = next(path.rglob("livery.cfg"), None)
    if cfg is None:
        return -1
    tag = IniFile(cfg).get("SELECTION", "required_tags").strip().strip('"').strip().upper()
    return TAG_VARIANTS.get(tag, -1)


def is_installed(livery_dir, folder):
    return bool(folder) and child(Path(livery_dir), folder).is_dir()


def thumbnail_in(folder):
    thumbnails = next((path for path in Path(folder).rglob("thumbnail") if path.is_dir()), None)
    if thumbnails is None:
        return None, False
    for name in ("thumbnail.jpg", "thumbnail.png"):
        if (thumbnails / name).is_file():
            return thumbnails / name, True
    return None, True


def scan_installed(livery_dir):
    livery_dir = Path(livery_dir)
    if not livery_dir.is_dir():
        return []
    found = []
    for folder in sorted(livery_dir.iterdir(), key=lambda path: path.name.upper()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        cfg = next(folder.rglob("livery.cfg"), None)
        if cfg is None:
            continue
        ini = IniFile(cfg)
        if not ini.has("FLTSIM.0", "atc_id"):
            continue
        image, has_thumbnail_dir = thumbnail_in(folder)
        if not has_thumbnail_dir:
            continue
        reg = ini.get("FLTSIM.0", "atc_id")
        found.append(Livery(name=ini.get("GENERAL", "name") or reg, reg=reg, folder=folder.name, image_path=image))
    return found


def zip_top_folder(names):
    for name in names:
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if len(parts) > 1 and PurePosixPath(*parts[1:]).name.lower() == "livery.cfg":
            return parts[0]
    return None


def read_zip_livery(zip_path, cache_dir):
    zip_path = Path(zip_path)
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        return None
    with archive:
        names = archive.namelist()
        top = zip_top_folder(names)
        if top is None:
            return None
        cfg_name = next(name for name in names if name.startswith(top + "/") and name.lower().endswith("/livery.cfg"))
        ini = IniFile(data=archive.read(cfg_name))
        if not ini.has("FLTSIM.0", "atc_id"):
            return None
        thumbnail_names = [name for name in names if name.startswith(top + "/") and "/thumbnail/" in name.lower()]
        if not thumbnail_names:
            return None
        image = None
        for wanted in ("thumbnail.jpg", "thumbnail.png"):
            match = next((name for name in thumbnail_names if PurePosixPath(name).name.lower() == wanted), None)
            if match:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
                image = Path(cache_dir) / wanted
                image.write_bytes(archive.read(match))
                break
        reg = ini.get("FLTSIM.0", "atc_id")
        return Livery(name=ini.get("GENERAL", "name") or reg, reg=reg, folder=safe_folder(top), zip_path=zip_path, image_path=image)


def local_zips(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted((path for path in folder.iterdir() if path.suffix.lower() == ".zip" and path.is_file()), key=lambda path: path.name.upper())


def classify_member(name, livery):
    normalized = name.replace("\\", "/").removeprefix("./")
    if normalized.endswith("/"):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"unsafe path inside the zip: {name}")
    parts = path.parts
    if len(parts) == 1:
        for kind in ("mda", "png"):
            if parts[0].lower() == f"{livery.reg}.{kind}".lower():
                return kind, PurePosixPath(f"{livery.reg}.{kind}")
        return None
    if parts[0].lower() == livery.folder.lower():
        return "livery", PurePosixPath(*parts[1:])
    return None


def install(zip_path, livery, files, progress=None):
    with PACKAGE_LOCK:
        install_locked(zip_path, livery, files, progress)


def install_locked(zip_path, livery, files, progress):
    safe_folder(livery.folder)
    livery_dir = Path(files.livery_dir)
    aircrafts_dir = Path(files.aircrafts_dir)
    staging = livery_dir.parent / f".{livery.folder}.installing"
    with zipfile.ZipFile(zip_path) as archive:
        plan = []
        total = 0
        for info in archive.infolist():
            member = classify_member(info.filename, livery)
            if member is None:
                continue
            kind, relative = member
            if kind == "livery":
                path = staging.joinpath(*relative.parts)
            else:
                path = child(aircrafts_dir, relative.name)
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ValueError("the unzipped files are larger than 1 GiB")
            plan.append((info, path, kind))
        if not any(kind == "livery" for _, _, kind in plan):
            raise ValueError(f"the zip has no {livery.folder} folder")
        if staging.exists():
            shutil.rmtree(staging)
        done = 0
        try:
            for info, path, kind in plan:
                if kind == "mda" and path.exists():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(path, "wb") as target:
                    shutil.copyfileobj(source, target, 1 << 20)
                done += info.file_size
                if progress:
                    progress(done, total)
            final = child(livery_dir, livery.folder)
            if final.exists():
                shutil.rmtree(final)
            livery_dir.mkdir(parents=True, exist_ok=True)
            staging.rename(final)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    write_layout(files.layout_package)


def uninstall(livery, files):
    safe_folder(livery.folder)
    with PACKAGE_LOCK:
        target = child(Path(files.livery_dir), livery.folder)
        if target.is_dir():
            shutil.rmtree(target)
        write_layout(files.layout_package)
