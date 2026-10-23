#!/usr/bin/env python3
"""Build vendor source packages using the Python standard library."""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent


def version() -> str:
    for statement in ast.parse((ROOT / "game_optimizer/__init__.py").read_text()).body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__"
                                                      for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str) and value and all(char.isdigit() or char == "." for char in value):
                return value
    raise ValueError("Cannot find the application version.")


def source_files() -> list[Path]:
    # Explicit paths exclude machine settings, caches and local artifacts.
    paths = [ROOT / name for name in (".gitignore", "README.md", "LICENSE", "RELEASE_NOTES.md", "game-optimizer",
                                      "assets/game-optimizer.svg", "tools/install.py", "tools/build_release.py")]
    paths.extend(sorted((ROOT / "game_optimizer").glob("*.py")))
    paths.extend(sorted((ROOT / "docs").rglob("*.md")))
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular release file: {path.relative_to(ROOT)}")
    return sorted(paths)


def build(output: Path, vendor: str = "nvidia") -> dict:
    if vendor not in ("nvidia", "amd", "intel"):
        raise ValueError("Choose nvidia, amd or intel.")
    app_version = version()
    release_version = app_version + "-" + vendor
    package = "vram-game-optimizer-" + release_version
    archive_name = package + "-linux"
    entries = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in source_files()}
    notes = ROOT / "docs/releases" / ("v" + release_version + ".md")
    if not notes.is_file():
        raise ValueError(f"Missing release notes for {release_version}.")
    entries["RELEASE_NOTES.md"] = notes.read_bytes()
    entries["release.json"] = (json.dumps({
        "name": "VRAM Game Optimizer", "version": app_version,
        "tag": "v" + release_version, "vendor": vendor, "platform": "linux",
        "memory_backend": {"amd":"amdgpu-sysfs", "nvidia":"nvidia-smi", "intel":"i915-xe-drm-query"}[vendor],
        "hardware_validation": "reference-system-only" if vendor == "nvidia" else "pending",
        "release_status": "preview",
        "license": "BSD-3-Clause", "python_requires": ">=3.10", "prerelease": True,
        "repository": "https://github.com/MASELKO-95/vram-game-optimizer",
    }, indent=2) + "\n").encode()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archives = [output / (archive_name + ".tar.gz"), output / (archive_name + ".zip")]
    executable = {"game-optimizer", "tools/install.py", "tools/build_release.py"}
    with archives[0].open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for relative, content in sorted(entries.items()):
                    info = tarfile.TarInfo(package + "/" + relative)
                    info.size = len(content)
                    info.mode = 0o755 if relative in executable else 0o644
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(content))
    with zipfile.ZipFile(archives[1], "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(entries.items()):
            info = zipfile.ZipInfo(package + "/" + relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if relative in executable else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    checksums = output / "SHA256SUMS"
    checksums.write_text("".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
                                 for path in archives))
    return {"tag": "v" + release_version, "root": package, "vendor": vendor,
            "notes": str(notes),
            "files": [str(path) for path in (*archives, checksums)], "source_files": len(entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", choices=("nvidia", "amd", "intel"), default="nvidia")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output or ROOT / "dist" / args.vendor, args.vendor), indent=2))


if __name__ == "__main__":
    main()
