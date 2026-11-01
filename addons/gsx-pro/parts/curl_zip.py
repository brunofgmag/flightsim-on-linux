import sys
import zipfile
from pathlib import Path


def extract(archive, target, names):
    with zipfile.ZipFile(archive) as bundle:
        for name in names:
            member = next((entry for entry in bundle.namelist() if entry.endswith(f"/bin/{name}")), None)
            if member is None:
                return False
            Path(target, name).write_bytes(bundle.read(member))
    return True


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: curl_zip.py <zip> <folder> <file name>...", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if extract(sys.argv[1], sys.argv[2], sys.argv[3:]) else 1)
