from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
ARCHIVE_URL = "https://github.com/tacticatrus-spec/ozon-pomoshchnik/archive/refs/heads/main.zip"
FILES = ("app.py", "requirements.txt", "run.bat", "updater.py", "tt_optimization.json", "static", "templates")


def main():
    with tempfile.TemporaryDirectory(prefix="ozon-update-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "update.zip"
        request = urllib.request.Request(ARCHIVE_URL, headers={"User-Agent": "Ozon-Pomoshchnik-Updater"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        extract_dir = tmp_path / "files"
        with zipfile.ZipFile(archive) as package:
            package.extractall(extract_dir)
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Не удалось определить папку обновления")
        source = roots[0]
        for name in FILES:
            src = source / name
            dst = APP_DIR / name
            if not src.exists():
                continue
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
    print("OZON Assistant успешно обновлён")


if __name__ == "__main__":
    main()
