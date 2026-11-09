#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build a self-contained, desktop-installable ZIP for a plugin with a local MCP server.

The package contains the plugin's tracked files, a pinned CPython 3.12 runtime verified by
SHA-256, and every MCP server dependency installed into that runtime for the target platform. Its
``mcp.json`` launches ``./runtime/...``, so installation needs no system Python, network access,
or package install. ``BUNDLE.json`` marks the package as bundled; the launchers then refuse to
install anything at runtime.

    python scripts/build_plugin_bundle.py dell-data-orchestration-engine --platform win_amd64
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# python-build-standalone 20261026, CPython 3.12.16, "install_only_stripped" builds.
RUNTIME_RELEASE = "20261026"
RUNTIME_VERSION = "3.12.16"
RUNTIMES = {
    "win_amd64": {
        "asset": f"cpython-{RUNTIME_VERSION}+{RUNTIME_RELEASE}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "sha256": "1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149",
        "python": "./runtime/python.exe",
        "site_packages": "runtime/Lib/site-packages",
        "pip_platforms": ["win_amd64"],
    },
    "linux_x86_64": {
        "asset": f"cpython-{RUNTIME_VERSION}+{RUNTIME_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "sha256": "ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20",
        "python": "./runtime/bin/python3.12",
        "site_packages": "runtime/lib/python3.12/site-packages",
        "pip_platforms": ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"],
    },
}
RUNTIME_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/{release}/{asset}"

# Source-only distributions in the dependency tree. They are pure Python, so they are built
# into universal wheels locally and then resolved like every binary wheel.
SOURCE_ONLY = ["docopt==0.6.2"]


def _doe_requirements(plugin: Path) -> list[str]:
    pyproject = tomllib.loads((plugin / "mcp-server" / "pyproject.toml").read_text(encoding="utf-8"))
    return list(pyproject["project"]["dependencies"])


def _workspace_requirements(plugin: Path) -> list[str]:
    lines = (plugin / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


PLUGINS = {
    "dell-ai-factory-agentic-workspace": {"server": "dell_devspaces", "requirements": _workspace_requirements},
    "dell-data-orchestration-engine": {"server": "dell_dataloop", "requirements": _doe_requirements},
}
# Development-only content that the installed plugin never uses.
EXCLUDED_PARTS = {"tests", "validation", "evals", "docs", "specs", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_runtime(platform: str, cache: Path) -> Path:
    spec = RUNTIMES[platform]
    archive = cache / spec["asset"]
    if not archive.is_file() or sha256(archive) != spec["sha256"]:
        cache.mkdir(parents=True, exist_ok=True)
        url = RUNTIME_URL.format(release=RUNTIME_RELEASE, asset=spec["asset"].replace("+", "%2B"))
        with urllib.request.urlopen(url, timeout=300) as response, archive.open("wb") as stream:  # noqa: S310
            shutil.copyfileobj(response, stream)
    actual = sha256(archive)
    if actual != spec["sha256"]:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"runtime checksum mismatch for {spec['asset']}: {actual}")
    return archive


def tracked_files(plugin: str) -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", plugin],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    files = []
    for name in filter(None, listed):
        relative = Path(name).relative_to(plugin)
        if not EXCLUDED_PARTS.intersection(relative.parts) and (ROOT / name).is_file():
            files.append(relative)
    return files


def install_dependencies(platform: str, requirements: list[str], target: Path, work: Path) -> list[dict]:
    wheelhouse = work / "wheelhouse"
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--disable-pip-version-check",
         "-w", str(wheelhouse), *SOURCE_ONLY],
        check=True,
    )
    report = work / "pip-report.json"
    platforms = [arg for tag in RUNTIMES[platform]["pip_platforms"] for arg in ("--platform", tag)]
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         "--target", str(target), "--python-version", "3.12", "--implementation", "cp",
         "--only-binary=:all:", "--find-links", str(wheelhouse), "--no-compile",
         "--report", str(report), *platforms, *requirements],
        check=True,
    )
    installed = json.loads(report.read_text(encoding="utf-8"))["install"]
    return sorted(
        (
            {
                "name": item["metadata"]["name"],
                "version": item["metadata"]["version"],
                "license": item["metadata"].get("license_expression") or item["metadata"].get("license") or "",
                "sha256": (item.get("download_info", {}).get("archive_info", {}).get("hashes") or {}).get("sha256"),
            }
            for item in installed
        ),
        key=lambda entry: entry["name"].lower(),
    )


def write_notices(path: Path, packages: list[dict]) -> None:
    lines = [
        "Third-party software bundled in this package. Each distribution's own license and",
        "notice files are kept in its *.dist-info directory under the runtime's site-packages.",
        "",
        f"CPython {RUNTIME_VERSION} (python-build-standalone {RUNTIME_RELEASE}) - PSF-2.0 and",
        "component licenses listed in runtime/ (see python-build-standalone release notes).",
        "",
    ]
    for package in packages:
        licence = " ".join(package["license"].split())[:120] or "see dist-info"
        lines.append(f"{package['name']} {package['version']} - {licence}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(plugin: str, platform: str, output: Path, cache: Path) -> Path:
    if plugin not in PLUGINS:
        raise SystemExit(f"{plugin} has no local MCP server to bundle")
    source = ROOT / plugin
    manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    archive = fetch_runtime(platform, cache)
    with tempfile.TemporaryDirectory(prefix="bundle-") as scratch:
        work = Path(scratch)
        stage = work / plugin
        for relative in tracked_files(plugin):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        with tarfile.open(archive) as tar:
            tar.extractall(work / "extracted", filter="data")
        shutil.move(str(work / "extracted" / "python"), str(stage / "runtime"))
        site_packages = stage / RUNTIMES[platform]["site_packages"]
        packages = install_dependencies(platform, PLUGINS[plugin]["requirements"](source), site_packages, work)

        config = json.loads((stage / "mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"][PLUGINS[plugin]["server"]]
        server["command"] = RUNTIMES[platform]["python"]
        # The plugin cache is read-only to the server: never write bytecode or user site paths.
        server["env"] = {**server.get("env", {}), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        (stage / "mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        write_notices(stage / "THIRD_PARTY_NOTICES.txt", packages)
        (stage / "BUNDLE.json").write_text(
            json.dumps(
                {
                    "plugin": plugin,
                    "version": version,
                    "platform": platform,
                    "runtime": {
                        "implementation": "CPython",
                        "version": RUNTIME_VERSION,
                        "source": RUNTIME_URL.format(release=RUNTIME_RELEASE, asset=RUNTIMES[platform]["asset"]),
                        "sha256": RUNTIMES[platform]["sha256"],
                    },
                    "packages": packages,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        output.mkdir(parents=True, exist_ok=True)
        target = output / f"{plugin}-{version}-{platform}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            # Symlinks (bin/python3 -> python3.12, libpython3.12.so -> .so.1.0) are aliases of
            # files already stored; installers do not reliably recreate links from a ZIP, and the
            # command and the dynamic loader use the real names.
            for path in sorted(p for p in stage.rglob("*") if p.is_file() and not p.is_symlink()):
                info = zipfile.ZipInfo.from_file(path, f"{plugin}/{path.relative_to(stage).as_posix()}")
                info.date_time = (2026, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as stream:
                    bundle.writestr(info, stream.read(), compresslevel=9)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plugins", nargs="+", choices=sorted(PLUGINS))
    parser.add_argument("--platform", action="append", choices=sorted(RUNTIMES), dest="platforms")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--cache", type=Path, default=ROOT / ".bundle-cache")
    args = parser.parse_args()
    for plugin in args.plugins:
        for platform in args.platforms or sorted(RUNTIMES):
            path = build(plugin, platform, args.output, args.cache)
            print(f"{path} {path.stat().st_size // (1 << 20)} MiB sha256={sha256(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
