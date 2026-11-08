#!/usr/bin/env python3
"""Stage the offline @usemont/cli launcher and verified native payload packages."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import release


PACKAGE = "@usemont/cli"
TARGETS = {
    "aarch64-apple-darwin": ("darwin-arm64", "darwin", "arm64"),
    "x86_64-pc-windows-msvc": ("win32-x64", "win32", "x64"),
    "x86_64-unknown-linux-gnu": ("linux-x64", "linux", "x64"),
    "aarch64-unknown-linux-gnu": ("linux-arm64", "linux", "arm64"),
}


def prepare(archives, output, approved_terms_sha256=None, *, internal=False):
    """Atomically stage a complete public release or a private local subset.

    Portable release verification validates immutable archives and external trust;
    native release acceptance remains required on each target before publication.
    """
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("npm output directory must not exist")
    if type(internal) is not bool:
        raise ValueError("internal mode must be explicitly true or false")
    if not internal and approved_terms_sha256 is None:
        raise ValueError("Public npm staging requires independently approved terms SHA256")
    candidates = {}
    for archive in archives:
        archive = Path(archive).absolute()
        manifest = release.read_json(archive.parent / "release.json")
        release.validate_manifest(manifest, archive.name, not internal)
        target = manifest["target"]
        if target not in TARGETS:
            raise ValueError("Unsupported npm payload target")
        if target in candidates:
            raise ValueError("Duplicate npm payload target")
        required_editor = {"editor/" + name for name in release.EDITOR_REQUIRED | release.EDITOR_SBOMS}
        if not required_editor <= manifest["files"].keys():
            raise ValueError("npm distribution requires the complete bundled editor")
        if approved_terms_sha256 is not None and manifest["termsSha256"] != approved_terms_sha256:
            raise ValueError("Packaged terms do not match the independently approved SHA256")
        candidates[target] = (archive, manifest)
    if not candidates:
        raise ValueError("At least one native archive is required")
    if not internal and candidates.keys() != TARGETS.keys():
        raise ValueError("Public npm staging requires all four native payloads")
    first = next(iter(candidates.values()))[1]
    identity = ("version", "sourceCommit", "sourceDirty", "termsSha256", "launcherVersion")
    for _, manifest in candidates.values():
        if any(manifest[key] != first[key] for key in identity):
            raise ValueError("Native archives differ in version, source provenance, terms or companion version")
    version = first["version"]
    common = {
        "version": version,
        "description": "Native Mont command-line video editor",
        "license": "SEE LICENSE IN TERMS.txt",
        "homepage": "https://github.com/usemont/mont-cli",
        "repository": {"type": "git", "url": "https://github.com/usemont/mont-cli.git"},
        "engines": {"node": ">=22"},
    }
    if internal:
        common["private"] = True
    else:
        common["publishConfig"] = {"access": "public", "registry": "https://registry.npmjs.org/"}
    with tempfile.TemporaryDirectory(prefix="mont-npm-", dir=output.absolute().parent) as temporary:
        stage = Path(temporary) / "packages"
        stage.mkdir()
        dependencies = {}
        first_root = None
        for target, (archive, manifest) in candidates.items():
            suffix, operating_system, cpu = TARGETS[target]
            with redirect_stdout(io.StringIO()):
                report = release.verify(SimpleNamespace(archive=archive, require_signed=not internal,
                    extract=Path(temporary) / ("verified-" + suffix), portable=True))
            if report["manifest"] != manifest:
                raise ValueError("Release metadata changed during verification")
            root = Path(report["root"])
            name = PACKAGE + "-" + suffix
            package = {**common, "name": name, "os": [operating_system], "cpu": [cpu],
                       "files": sorted([*manifest["files"], "release.json"])}
            if operating_system == "linux":
                package["libc"] = ["glibc"]
            (root / "package.json").write_bytes(release.encode(package))
            (root / "release.json").write_bytes(release.encode(manifest))
            destination = stage / ("cli-" + suffix)
            root.rename(destination)
            if first_root is None:
                first_root = destination
            dependencies[name] = version
        root = stage / "cli"
        root.mkdir()
        package = {**common, "name": PACKAGE, "bin": {"mont": "mont.cjs"},
                   "files": ["mont.cjs", "TERMS.txt", "README.md"],
                   "optionalDependencies": dict(sorted(dependencies.items()))}
        (root / "package.json").write_bytes(release.encode(package))
        shutil.copyfile(first_root / "TERMS.txt", root / "TERMS.txt")
        (root / "README.md").write_text(
            "# Mont CLI\n\n"
            + ("Private local acceptance package; not a public release.\n\n" if internal else "")
            + f"Run this exact release with Node.js 22 or newer:\n\n"
            f"```sh\nnpx --yes --ignore-scripts {PACKAGE}@{version} --help\n```\n\n"
            "Supported: Apple Silicon macOS 14+, Windows x64, and GNU Linux x64/ARM64 "
            "(glibc 2.36+, Debian 12 baseline). Intel macOS, Windows ARM64, and musl "
            "are not supported. Keep npm optional dependencies enabled; no lifecycle "
            "scripts, Rust compiler, or binary download hooks are required.\n\n"
            "Linux also needs the OpenSSL 3 and C++ runtimes. Rendering needs a "
            "Vulkan loader and driver; Mesa's software driver supports CPU-only "
            "machines. Unlike APT, npm does not install OS dependencies.\n\n"
            "Media import and rendering require separately installed FFmpeg/ffprobe "
            "and a supported native GPU backend. npm does not install these system "
            "dependencies. Other commands do not require FFmpeg.\n\n"
            "## Updates\n\n"
            "The installer owns updates. Global npm installations use "
            f"`npm install -g {PACKAGE}@preview`; project installations update their "
            "dependency and lockfile explicitly. For one-off execution, select an "
            "exact version or the `preview` channel; `latest` is reserved for stable "
            "releases. Bare npx can select a project-local installation. Normal "
            "startup does not check for updates or replace another installation.\n\n"
            "Homebrew installations update with `brew update && brew upgrade "
            "usemont/tap/mont`. Both installations may coexist; PATH determines "
            "plain `mont`, while this npm launcher always uses its own exact-version "
            "payload. Do not use npm `--force` to replace Homebrew's bin link; use "
            "npx or a separate npm prefix instead. `mont mcp install` explicitly refreshes the shared per-user "
            "MCP launcher; upgrading npm alone does not refresh it.\n\n"
            "Mont is proprietary, binary-only software. See TERMS.txt and the native "
            "payload's README.md, notices, SBOMs, and release.json. Installing this "
            "package grants no access to the private source or SDK.\n",
            encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("npm-launcher.cjs"), root / "mont.cjs")
        (root / "mont.cjs").chmod(0o755)
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New npm package-set directory")
    parser.add_argument("--approved-terms-sha256")
    parser.add_argument("--internal", action="store_true", help="Private local acceptance only; never publish")
    args = parser.parse_args()
    try:
        print(prepare(args.archive, args.output, args.approved_terms_sha256, internal=args.internal))
    except (OSError, ValueError, KeyError, release.ReleaseError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.exit(1, f"npm-package: {detail}\n")


if __name__ == "__main__":
    main()
