#!/usr/bin/env python3
"""Package supplied native builds and reviewed third-party sources; never build.

Checksums detect corruption, not publisher identity. Public verification additionally
requires Apple's notarized Developer ID assessment or Linux/Windows OpenPGP signatures
with independently pinned platform trust. Verification never executes unsigned payloads.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import platform
import runpy
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import signing

CLI = Path(__file__).resolve().parents[1]
MCP = CLI.parent / "mont-mcp"
TARGET = "aarch64-apple-darwin"
TARGETS = {
    TARGET: ("darwin-arm64", "bin/mont"),
    "aarch64-unknown-linux-gnu": ("linux-arm64", "bin/mont"),
    "x86_64-unknown-linux-gnu": ("linux-x64", "bin/mont"),
    "x86_64-pc-windows-msvc": ("windows-x64", "bin/mont.exe"),
}
RUST = "+1.91.0"
# External command names are not linked implementations. Keep the broader Cargo/
# SBOM denylist separate from implementation symbols/banners in the executable.
MEDIA_IMPLEMENTATION = re.compile(rb"ffmpeg(?:_version| version|\.c\b)|libav(?:codec|format|util|device|filter|resample)\b|avcodec|avformat|avutil|swscale|swresample|gst[-_]libav|gstreamer|openh264|x264_encoder|x265_encoder", re.I)
FORBIDDEN = re.compile(rb"ffmpeg|ffprobe|libav\b|" + MEDIA_IMPLEMENTATION.pattern, re.I)
RUNTIME_EXECUTABLES = ["ffmpeg", "ffprobe"]
PAYLOAD = {"bin/mont", "bin/mont-mcp", "README.md", "example/workflow.mont.json", "example/video-workflow.mont.json", "example/vertical.inputs.json", "SBOM.json", "MCP-SBOM.json", "THIRD_PARTY_NOTICES.txt", "TERMS.txt"}
EDITOR_REQUIRED = {
    "index.html", "flutter.js", "flutter_bootstrap.js", "main.dart.js",
    "assets/AssetManifest.bin", "assets/FontManifest.json", "assets/NOTICES",
    "canvaskit/canvaskit.js", "canvaskit/canvaskit.wasm",
    "renderer_wasm/mont_editor_web.js", "renderer_wasm/mont_editor_web_bg.wasm",
    "editor_runtime_wasm/mont_editor_runtime_web.js",
    "editor_runtime_wasm/mont_editor_runtime_web_bg.wasm",
    "vendor/LICENSE.mp4box", "vendor/LICENSE.mp4-muxer",
    "editor-build.json", "wasm-source-manifest.json",
}
EDITOR_SBOMS = {"web_renderer-SBOM.json", "editor_runtime_web-SBOM.json"}
# Emscripten's quoted virtual home is not a host path; its subpaths remain audited.
PRIVATE_PATH = re.compile(rb"/(?:Users|home(?!/web_user\")|private/tmp|tmp|opt/homebrew)/[^\s\x00]+|(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[A-Za-z0-9_. -]+[\\/])+[A-Za-z0-9_.-]{2,}|(?<![A-Za-z0-9]\x00)[A-Za-z]\x00:\x00[\\/]\x00(?:(?:[A-Za-z0-9_. -]\x00)+[\\/]\x00)+(?:[A-Za-z0-9_.-]\x00){2,}|\\\\[A-Za-z0-9_.-]+\\[A-Za-z0-9_. -]+\\[A-Za-z0-9_.-]{2,}")
VERSION = re.compile(r"0\.[0-9]+\.[0-9]+-preview\.[1-9][0-9]*")
HEX = re.compile(r"[0-9a-f]{64}")
FINGERPRINT = re.compile(r"[0-9A-F]{40}")
LICENSES = {"MIT", "Apache-2.0", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Unicode-3.0", "Zlib", "BSL-1.0", "Unlicense", "CDLA-Permissive-2.0"}
INTERNAL_TERMS = """Mont CLI — internal engineering artifact only.
Proprietary. No public distribution or redistribution permission is granted by
this archive. No approved Developer Preview license is included. This unsigned
artifact is not a public release and must not be used to bypass macOS Gatekeeper.
Third-party components remain governed by their respective notices.
"""


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def run(args, *, cwd=None):
    # Native tools can echo keychain profiles, URLs or private paths on failure.
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "LC_ALL": "C"})
    require(result.returncode == 0, f"{Path(str(args[0])).name}: command failed (diagnostics withheld)")
    return result.stdout if result.stdout else result.stderr


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique)


def safe_path(name):
    require(isinstance(name, str) and name and "\\" not in name, "invalid payload path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(p not in ("", ".", "..") for p in name.split("/")), "unsafe payload path")
    require(all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts), "unexpected payload path characters")
    return path


def native_target():
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return TARGET
    if sys.platform.startswith("linux") and platform.libc_ver()[0] == "glibc":
        if machine in {"arm64", "aarch64"}:
            return "aarch64-unknown-linux-gnu"
        if machine in {"x86_64", "amd64"}:
            return "x86_64-unknown-linux-gnu"
    # Windows 11 ARM64 can run this x64 candidate through OS emulation; this
    # does not claim native ARM64 binary support or native x64 hardware testing.
    if sys.platform == "win32" and machine in {"amd64", "x86_64", "arm64", "aarch64"}:
        return "x86_64-pc-windows-msvc"
    raise ReleaseError("unsupported native release host/architecture")


def executable_path(target, program="mont"):
    require(isinstance(target, str) and target in TARGETS, "unsupported release target")
    require(program in {"mont", "mont-mcp"}, "unsupported release executable")
    return "bin/" + program + (".exe" if target.endswith("-windows-msvc") else "")


def executable_paths(target):
    return {executable_path(target, program) for program in ("mont", "mont-mcp")}


def payload_paths(target):
    return (PAYLOAD - {"bin/mont", "bin/mont-mcp"}) | executable_paths(target)


def validate_payload_paths(files, target):
    base = payload_paths(target)
    extra = set(files) - base
    require(base <= set(files), "unexpected/incomplete release files")
    source_paths = {name for name in extra if name.startswith("third-party-sources/")}
    if source_paths:
        audit = read_json(CLI / "tool/native-notices.json")
        reviewed = {f"third-party-sources/{p['name']}-{p['version']}.crate" for p in audit["packages"] if p.get("selectedLicense", p.get("license")) == "MPL-2.0"}
        require(source_paths <= reviewed, "unexpected third-party source archive")
    for name in extra:
        path = safe_path(name)
        require(name.startswith("editor/") or name in source_paths, "unexpected release file")
        require(not ({"fixtures", "test_fixtures"} & set(path.parts)) and name not in {
            "editor/assets/packages/mont_sdk/assets/editor-runtime-first-slice-project.json",
            "editor/assets/packages/mont_sdk/assets/scrub_scheduler_traces.json",
        }, f"development fixture in release payload: {name}")
    editor_paths = extra - source_paths
    if editor_paths:
        require({"editor/" + name for name in EDITOR_REQUIRED | EDITOR_SBOMS} <= editor_paths, "incomplete runnable editor assets")


def editor_files(directory):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "built editor directory required; run make mont-editor web-cli-build")
    files = []
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "symlink in editor assets")
        safe_path(path.relative_to(directory).as_posix())
        require(path.is_file() or path.is_dir(), "non-regular editor asset")
        if path.is_file():
            require(path.stat().st_size > 0, "empty editor asset")
            files.append(path)
    require(EDITOR_REQUIRED <= {p.relative_to(directory).as_posix() for p in files}, "incomplete runnable editor assets")
    return files


def check_editor_provenance(directory):
    """Bind supplied assets (including EDITOR_DIR overrides) to this checkout."""
    repo = CLI.parents[1]
    tools = CLI.parent / "mont-editor/tool"
    identity = read_json(directory / "editor-build.json")
    source = runpy.run_path(str(tools / "editor_build_identity.py"))
    require(identity.get("schemaVersion") == 1, "unsupported editor build identity")
    require(identity.get("dartSourceDigest") == source["dart_source_identity"](repo), "editor Dart/SDK source differs from this checkout; rebuild web-cli")
    require(identity.get("webSourceDigest") == source["_digest"](repo, source["_regular_files"](CLI.parent / "mont-editor/web")), "editor web/SDK assets differ from this checkout; rebuild web-cli")
    require(identity.get("mainDartSha256") == digest(directory / "main.dart.js"), "editor compiled Dart differs from verified build")
    wasm = read_json(directory / "wasm-source-manifest.json")
    freshness = runpy.run_path(str(tools / "web_wasm_freshness.py"))
    require(identity.get("wasm") == wasm == freshness["current_manifest"](), "editor WASM/SDK source differs from this checkout; rebuild web-cli")
    for relative, checksum in wasm["artifacts"].items():
        asset = directory / Path(relative).relative_to("projects/mont-editor/web")
        require(asset.is_file() and digest(asset) == checksum, "editor WASM asset differs from verified build")

def check_elf(binary, target):
    header = run(["readelf", "-hW", binary])
    machine = "AArch64" if target.startswith("aarch64-") else "Advanced Micro Devices X86-64"
    for field, value in (("Class", "ELF64"), ("Data", "2's complement, little endian"), ("Machine", machine)):
        require(re.search(rf"^\s*{field}:\s*{re.escape(value)}\s*$", header, re.M), "ELF architecture/header differs from target")
    require(re.search(r"^\s*Type:\s*(?:EXEC|DYN)\s", header, re.M), "binary is not an ELF executable")
    segments = run(["readelf", "-lW", binary])
    loaders = ("/lib/ld-linux-aarch64.so.1", "/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1") if target.startswith("aarch64-") else ("/lib64/ld-linux-x86-64.so.2", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
    require(re.findall(r"\[Requesting program interpreter: ([^\]]+)\]", segments) in [[loader] for loader in loaders], "unexpected/missing GNU ELF interpreter")
    dynamic = run(["readelf", "-dW", binary])
    require(not re.search(r"\((?:RPATH|RUNPATH|AUDIT|DEPAUDIT|FILTER|AUXILIARY)\)", dynamic), "runtime search paths/audit libraries are not allowed")
    libraries = re.findall(r"\(NEEDED\).*Shared library: \[([^\]]+)\]", dynamic)
    allowed = {"libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "librt.so.1", "libutil.so.1", "libresolv.so.2", "libgcc_s.so.1", "libstdc++.so.6", "libssl.so.3", "libcrypto.so.3"}
    allowed.add(Path(loaders[0]).name)
    require(libraries and all(name in allowed for name in libraries), "non-system ELF dynamic library")
    return libraries


def check_pe(data):
    """Bounded PE32+ parsing; never load or execute an untrusted Windows image."""
    def unpack(fmt, offset):
        require(0 <= offset <= len(data) - struct.calcsize(fmt), "truncated PE image")
        return struct.unpack_from(fmt, data, offset)
    require(data[:2] == b"MZ", "binary is not a PE executable")
    pe, = unpack("<I", 0x3c)
    require(pe >= 64 and data[pe:pe + 4] == b"PE\0\0", "invalid PE signature")
    machine, count, _, _, _, size, flags = unpack("<HHIIIHH", pe + 4)
    require(machine == 0x8664 and 1 <= count <= 96 and flags & 2 and not flags & 0x2000, "only x64 PE executables are supported")
    optional = pe + 24
    magic, = unpack("<H", optional)
    directories, = unpack("<I", optional + 108)
    subsystem, = unpack("<H", optional + 68)
    require(magic == 0x20b and size >= 240 and directories == 16 and subsystem == 3, "unsupported PE optional header/subsystem")
    require(optional + size <= len(data), "truncated PE optional header")
    sections = []
    for index in range(count):
        section = optional + size + index * 40
        _, address, raw_size, raw = unpack("<IIII", section + 8)
        require(section + 40 <= len(data) and raw + raw_size <= len(data), "truncated PE section")
        sections.append((address, raw_size, raw))
    def offset(rva, length):
        matches = [raw + rva - address for address, size, raw in sections if address <= rva and rva + length <= address + size]
        require(length > 0 and len(matches) == 1, "invalid/ambiguous PE data address")
        return matches[0]
    certificate_start, certificate_size = unpack("<II", optional + 112 + 4 * 8)
    if certificate_start or certificate_size:
        # Structure only: native Windows acceptance checks Authenticode identity,
        # timestamp and trust. Detached archive signatures remain mandatory.
        image_end = max(optional + size + count * 40, *(raw + length for _, length, raw in sections))
        require(certificate_start >= image_end and certificate_start % 8 == 0
                and certificate_size > 0 and certificate_size % 8 == 0
                and certificate_start + certificate_size == len(data), "invalid PE certificate table bounds")
        cursor = certificate_start
        while cursor < len(data):
            length, revision, kind = unpack("<IHH", cursor)
            padded = (length + 7) & ~7
            require(length > 8 and revision == 0x0200 and kind == 0x0002
                    and cursor + padded <= len(data), "invalid PE WIN_CERTIFICATE record")
            require(not any(data[cursor + length:cursor + padded]), "nonzero PE certificate padding")
            cursor += padded
    delay = unpack("<II", optional + 112 + 13 * 8)
    require(delay == (0, 0), "unreviewed PE delay-load imports are not allowed")
    imports, import_size = unpack("<II", optional + 112 + 8)
    require(imports and 20 <= import_size <= 65536, "missing/oversized PE import directory")
    start = offset(imports, import_size)
    libraries = []
    allowed = {"advapi32.dll", "bcrypt.dll", "bcryptprimitives.dll", "cfgmgr32.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll", "d3d11.dll", "d3d12.dll", "d3dcompiler_47.dll", "dcomp.dll", "dbghelp.dll", "dwmapi.dll", "dwrite.dll", "dxgi.dll", "gdi32.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "mswsock.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll", "opengl32.dll", "powrprof.dll", "propsys.dll", "rpcrt4.dll", "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll", "ucrtbase.dll", "user32.dll", "userenv.dll", "usp10.dll", "version.dll", "windowscodecs.dll", "winhttp.dll", "winmm.dll", "winspool.drv", "wintrust.dll", "ws2_32.dll", "wtsapi32.dll"}
    for entry in range(start, start + import_size - 19, 20):
        descriptor = unpack("<IIIII", entry)
        if not any(descriptor):
            break
        name = offset(descriptor[3], 1)
        end = data.find(b"\0", name, min(name + 256, len(data)))
        require(end > name, "invalid PE import name")
        offset(descriptor[3], end - name + 1)
        dll = data[name:end].decode("ascii").lower()
        require(dll in allowed or re.fullmatch(r"(?:api|ext)-ms-win-[a-z0-9-]+-l[0-9]+-[0-9]+-[0-9]+\.dll", dll), "non-system PE import; external runtime redistribution is not supported")
        libraries.append(dll)
    else:
        raise ReleaseError("unterminated PE import directory")
    require(libraries, "no system PE imports found")
    return libraries


def binary_contents(binary):
    require(binary.is_file() and not binary.is_symlink(), "binary must be a regular file")
    data = binary.read_bytes()
    require(not MEDIA_IMPLEMENTATION.search(data), "forbidden media implementation in executable")
    require(not PRIVATE_PATH.search(data), "private build paths in executable; remap Rust and C/C++ source/debug paths")
    return data


def check_portable_binary(binary, target):
    """Inspect bytes without host linkage tools or executing the candidate."""
    executable_path(target)
    data = binary_contents(binary)
    if target.endswith("-windows-msvc"):
        return check_pe(data)
    if target.endswith("-linux-gnu"):
        require(len(data) >= 64 and data[:7] == b"\x7fELF\x02\x01\x01", "64-bit little-endian ELF required")
        kind, machine = struct.unpack_from("<HH", data, 16)
        require(kind in (2, 3) and machine == (183 if target.startswith("aarch64-") else 62), "ELF architecture/type differs from target")
    else:
        require(len(data) >= 32, "truncated Mach-O header")
        magic, cpu, _, kind = struct.unpack_from("<IIII", data)
        require((magic, cpu, kind) == (0xfeedfacf, 0x100000c, 2), "thin ARM64 Mach-O executable required")
    # Native linkage and Apple signature/notarization acceptance remain mandatory
    # in the target's own release job; portable staging cannot establish them.
    return None


def check_binary(binary, *, target=TARGET, execute=False, version=None, program="mont"):
    executable_path(target)
    data = binary_contents(binary)
    if target.endswith("-linux-gnu"):
        libraries = check_elf(binary, target)
    elif target.endswith("-windows-msvc"):
        libraries = check_pe(data)
    else:
        require(run(["lipo", "-archs", binary]).strip() == "arm64", "only thin ARM64 Mach-O is supported")
        require(re.search(r"\b(?:MH_)?EXECUTE\b", run(["otool", "-hv", binary])), "binary is not a Mach-O executable")
        libraries = []
        for line in run(["otool", "-L", binary]).splitlines()[1:]:
            name = line.strip().split(" (", 1)[0]
            require(name.startswith(("/usr/lib/", "/System/Library/")) and ".." not in name.split("/"), "non-system dynamic library")
            libraries.append(name)
        require(libraries, "no system dynamic dependencies found")
        commands = run(["otool", "-l", binary])
        require("LC_RPATH" not in commands and "LC_DYLD_ENVIRONMENT" not in commands, "runtime search paths/environment are not allowed")
    if execute:
        require(run([binary, "--version"]).strip() == f"{program} {version}", "runtime version differs from artifact version")
    return libraries


def signature(binary, expected=None):
    run(["codesign", "--verify", "--strict", "--verbose=2", binary])
    details = run(["codesign", "-d", "--verbose=4", binary])
    authority = re.search(r"^Authority=(Developer ID Application: .+)$", details, re.M)
    team = re.search(r"^TeamIdentifier=([A-Z0-9]{10})$", details, re.M)
    require(authority and team and re.search(r"flags=.*\bruntime\b", details) and "Timestamp=" in details, "Developer ID, secure timestamp and hardened runtime are required")
    result = {"identity": authority.group(1), "teamId": team.group(1)}
    if expected:
        require(all(expected.get(k) == v for k, v in result.items()), "signature identity differs from release manifest")
    return result


def assess(binary):
    # spctl's execute assessment expects an app bundle, not a standalone CLI.
    run(["codesign", "--verify", "--strict", "-R=notarized", "--check-notarization", "--verbose=4", binary])


def verify_openpgp_signature(signature_path, data_path, fingerprint, *, target):
    """Verify with independently pinned platform trust; never use archive keys."""
    executable_path(target)
    require(target != TARGET, "Apple releases require Developer ID, not OpenPGP")
    platform_name = "WINDOWS" if target.endswith("-windows-msvc") else "LINUX"
    trusted = os.environ.get(f"MONT_CLI_{platform_name}_SIGNING_FINGERPRINT", "")
    keyring = Path(os.environ.get(f"MONT_CLI_{platform_name}_KEYRING", ""))
    require(isinstance(fingerprint, str) and FINGERPRINT.fullmatch(fingerprint) and trusted == fingerprint, f"independently pinned {platform_name} signing fingerprint required")
    require(keyring.is_absolute() and keyring.is_file() and not keyring.is_symlink(), f"absolute exported {platform_name} public keyring required")
    signature_path, data_path = Path(signature_path), Path(data_path)
    require(signature_path.is_file() and not signature_path.is_symlink() and data_path.is_file() and not data_path.is_symlink(), "regular signed data and detached signature required")
    with tempfile.TemporaryDirectory(prefix="mont-gpgv-") as home:
        status = run(["gpgv", "--homedir", home, "--keyring", keyring, "--status-fd", "1", signature_path, data_path])
    valid = [line.split() for line in status.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
    require(len(valid) == 1 and len(valid[0]) in (11, 12), "one valid OpenPGP signature required")
    record = valid[0]
    primary = record[11] if len(record) == 12 else record[2]
    require(primary == trusted and record[9] == "8", "OpenPGP signature signer or digest differs from pinned policy")


def select_license(expression):
    """Elect an allowed OR branch; AND obligations and SPDX precedence remain."""
    legacy = re.fullmatch(r"(MIT|Apache-2\.0)\s*/\s*(MIT|Apache-2\.0)", expression.strip())
    if legacy:
        expression = " OR ".join(legacy.groups())
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*|[()]|\S", expression)
    position = 0
    def atom():
        nonlocal position
        token = tokens[position]
        position += 1
        if token == "(":
            result = alternative()
            if tokens[position] != ")":
                raise ValueError("unclosed SPDX group")
            position += 1
            return result
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", token) or token in {"AND", "OR", "WITH"}:
            raise ValueError("invalid SPDX identifier")
        result = token if token in LICENSES else None
        if position < len(tokens) and tokens[position] == "WITH":
            position += 1
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*", tokens[position]) or tokens[position] in {"AND", "OR", "WITH"}:
                raise ValueError("invalid SPDX exception")
            position += 1
            return None  # Exceptions require an explicit review, never token removal.
        return result
    def conjunction():
        nonlocal position
        result = atom()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            right = atom()
            result = f"({result} AND {right})" if result and right else None
        return result
    def alternative():
        nonlocal position
        result = conjunction()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            right = conjunction()
            result = result or right
        return result
    try:
        result = alternative()
        return result if position == len(tokens) else None
    except (IndexError, ValueError):
        return None


def license_allowed(expression):
    return select_license(expression) is not None


def selected_component_license(component):
    expression = component["licenses"][0]["expression"]
    selected = select_license(expression)
    if selected or expression not in {"CC0-1.0", "MPL-2.0", "MPL-2.0+", "BSD-3-Clause/MIT"}:
        return selected
    # These declarations require review of each exact published component.
    audit = read_json(CLI / "tool/native-notices.json")
    for record in audit["packages"]:
        elected = record.get("selectedLicense", record.get("license"))
        if (record.get("license"), elected) not in {("CC0-1.0", "CC0-1.0"), ("MPL-2.0", "MPL-2.0"), ("MPL-2.0+", "MPL-2.0"), ("BSD-3-Clause/MIT", "BSD-3-Clause")}:
            continue
        if record["license"] == expression and record["name"] == component["name"] and record["version"] == component.get("version") and component.get("hashes") == [{"alg": "SHA-256", "content": record["sha256"]}]:
            return elected
    return None


def legal_files(root):
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and re.search(r"(?:^|[._-])(licen[cs]e|copying|copyright|notice)(?:$|[._-])", p.name, re.I))


def upstream_license(package, root, cache):
    """Some crates omit the workspace license. Recover only its immutable revision."""
    repository = package.get("repository") or package.get("homepage") or ""
    parsed = urllib.parse.urlparse(repository.rstrip("/").removesuffix(".git"))
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/tree/[A-Za-z0-9_./-]+)?", parsed.path)
    # Legacy manifests can name HTTP GitHub URLs; retrieval is always HTTPS at
    # the cargo-recorded immutable revision, never an HTTP request or branch.
    require(parsed.scheme in {"https", "http"} and parsed.netloc == "github.com" and match and not parsed.query and not parsed.fragment, f"missing bundled notices: {package['name']}")
    require((root / ".cargo_vcs_info.json").is_file(), f"missing immutable notice provenance: {package['name']}")
    provenance = read_json(root / ".cargo_vcs_info.json")
    revision = provenance.get("git", {}).get("sha1", "")
    require(re.fullmatch(r"[0-9a-f]{40}", revision), "missing immutable crate revision")
    base = f"https://raw.githubusercontent.com/{match[1]}/{revision}/"
    if base in cache:
        return cache[base]
    notices = []
    for name in ("LICENSE", "LICENSE-MIT", "LICENSE-APACHE", "LICENSE-ZLIB", "LICENSE.MIT", "LICENSE.APACHE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYRIGHT", "COPYRIGHT.md", "NOTICE", "license/MIT", "license/APACHE"):
        url = base + name
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                require(response.url == url, "upstream notice redirect rejected")
                body = response.read(2 * 1024 * 1024 + 1)
            require(len(body) <= 2 * 1024 * 1024, "upstream notice too large")
            text = body.decode("utf-8")
            require("<html" not in text.lower() and len(text) > 50, "invalid upstream notice")
            notices.append((f"upstream@{revision}/{name}", text))
        except urllib.error.HTTPError as error:
            require(error.code == 404, f"cannot retrieve immutable notices: {package['name']}")
    require(notices, f"no immutable notices: {package['name']}")
    cache[base] = notices
    return notices


def audited_declared_notice(package, root, checksum, sources=None):
    """Exact reviewed declarations, not invented upstream copyright notices."""
    audit = read_json(CLI / "tool/native-notices.json")
    matches = [p for p in audit["packages"] if (p["name"], p["version"]) == (package["name"], package["version"])]
    if not matches:
        return []
    require(len(matches) == 1, "ambiguous audited native license declaration")
    record = matches[0]
    require(record.get("license", audit["license"]) == package.get("license"), "unaudited native license declaration")
    require(record["sha256"] == checksum and record["authors"] == package["authors"] and record["repository"] == package.get("repository"), "native notice provenance differs from audited crate")
    crate_name = f"{package['name']}-{package['version']}"
    archive = root.parents[2] / "cache" / root.parent.name / f"{crate_name}.crate"
    require(archive.is_file() and digest(archive) == checksum, "missing exact published native crate archive")
    with tarfile.open(archive) as original:
        for member in original.getmembers():
            relative = PurePosixPath(member.name)
            require(relative.parts[0] == crate_name and ".." not in relative.parts and (member.isfile() or member.isdir()), "invalid native crate provenance archive")
            if member.isfile():
                path = root.joinpath(*relative.parts[1:])
                require(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and path.is_file() and path.read_bytes() == original.extractfile(member).read(), "native crate source differs from published checksum")
    vcs = root / ".cargo_vcs_info.json"
    revision = read_json(vcs).get("git", {}).get("sha1") if vcs.is_file() else None
    require(revision == record["cargoVcsRevision"], "native crate VCS provenance differs from audit")
    elected = record.get("selectedLicense", package["license"])
    legal = audit.get("reviewedLicenses", {}).get(elected, audit)
    require(elected in {"MIT", "CC0-1.0"} or elected in audit.get("reviewedLicenses", {}) or "licenseSource" in record and "licenseText" in record, "missing reviewed license text")
    metadata = "\n".join((record.get("notice", audit["notice"]), f"Published crate SHA-256: {checksum}", f"Source: {record['source']}", f"Repository (declared): {record['repository']}", f"Cargo VCS revision: {revision or 'not present in published crate'}", "Declared authors (Cargo metadata): " + ", ".join(record["authors"]), f"Canonical license source: {record.get('licenseSource', legal['licenseSource'])}"))
    if elected == "MPL-2.0":
        source_path = f"third-party-sources/{crate_name}.crate"
        safe_path(source_path)
        metadata += f"\nBundled source: {source_path}\nSource provenance: Unmodified upstream .crate archive; published checksum verified."
        if sources is not None:
            require(source_path not in sources or digest(sources[source_path]) == checksum, "conflicting bundled source archives")
            sources[source_path] = archive
    return [(f"audited Cargo {package['license']} declaration", metadata + "\n\n" + record.get("licenseText", legal["licenseText"]))]


def embedded_font_notices(binary):
    """Only unchanged, individually audited OFL font bytes get this exception."""
    repo = CLI.parents[1]
    audit = read_json(CLI / "tool/embedded-fonts.json")
    require(audit["license"] == "OFL-1.1" and "SIL OPEN FONT LICENSE Version 1.1" in audit["licenseText"], "invalid audited font license")
    generated = (repo / "packages/font-catalog/generated/rust-embedded-fonts.rs").read_text(encoding="utf-8")
    declared = set(re.findall(r'"/../../(packages/font-catalog/assets/[^"]+\.ttf)"', generated))
    source = repo / "projects/text-renderer-core/src/rich_text.rs"
    declared.update((source.parent / name).resolve().relative_to(repo).as_posix() for name in re.findall(r'include_bytes!\("([^"]+\.ttf)"\)', source.read_text(encoding="utf-8")))
    fonts = audit["fonts"]
    require(len(fonts) == len({font["path"] for font in fonts}) and {font["path"] for font in fonts} == declared, "embedded font sources changed; audit required")
    data = binary.read_bytes()
    components, notices = [], []
    for font in fonts:
        path = repo / font["path"]
        require(path.resolve().is_relative_to(repo) and not path.is_symlink() and digest(path) == font["sha256"], "audited font bytes changed")
        require(font["copyright"] and font["licenseDeclarations"] and all("SIL Open Font License, Version 1.1" in text for text in font["licenseDeclarations"]), "missing authoritative font license evidence")
        if path.read_bytes() not in data:
            continue  # Linker-discarded inspection/test fonts are not redistributed.
        ref = "mont:embedded-font:" + font["sha256"]
        components.append({"type": "file", "bom-ref": ref, "name": font["path"], "hashes": [{"alg": "SHA-256", "content": font["sha256"]}], "licenses": [{"expression": "OFL-1.1"}], "properties": [{"name": "mont:provenance", "value": "Unmodified repository asset; authoritative copyright/license records in THIRD_PARTY_NOTICES.txt"}]})
        notices.append(f"\n{'=' * 72}\nEmbedded font: {font['path']}\nSHA-256: {font['sha256']}\nUnmodified font; OFL-1.1 (not the proprietary Mont license).\n")
        notices.extend(text + "\n" for key in ("copyright", "licenseDeclarations", "licenseUrls") for text in font[key])
    if components:
        notices.append("\n--- Complete embedded-font license: " + audit["licenseSource"] + " ---\n" + audit["licenseText"] + "\n")
    return components, notices


def dependency_notices(public, binary, target=TARGET, crate=CLI, sources=None, features=()):
    metadata = json.loads(run(["cargo", RUST, "metadata", "--locked", "--format-version", "1", "--filter-platform", target, *(["--features", ",".join(features)] if features else [])], cwd=crate))
    packages = {p["id"]: p for p in metadata["packages"]}
    nodes = {n["id"]: n for n in metadata["resolve"]["nodes"]}
    root_id = metadata["resolve"].get("root") or next(p["id"] for p in metadata["packages"] if Path(p["manifest_path"]).resolve() == (crate / "Cargo.toml").resolve())
    closure = set()
    def visit(pid):
        if pid in closure:
            return
        closure.add(pid)
        for dep in nodes[pid]["deps"]:
            if any(kind["kind"] is None for kind in dep["dep_kinds"]):
                visit(dep["pkg"])
    visit(root_id)
    lock = tomllib.loads((Path(metadata.get("workspace_root", crate)) / "Cargo.lock").read_text(encoding="utf-8"))
    checksums = {(p["name"], p["version"], p.get("source")): p.get("checksum") for p in lock["package"]}
    upstream_cache = {}
    components, dependencies, notices, unresolved = [], [], [], []
    refs = {pid: f"pkg:cargo/{packages[pid]['name']}@{packages[pid]['version']}" for pid in closure}
    for pid in sorted(closure, key=lambda x: refs[x]):
        p = packages[pid]
        require(not FORBIDDEN.search(p["name"].encode()), "forbidden dependency in Cargo closure")
        local = p["source"] is None
        require(not local or Path(p["manifest_path"]).resolve().is_relative_to(CLI.parents[1]), "external path dependency is forbidden")
        expression = "LicenseRef-Mont-Proprietary" if local else p.get("license") or "NOASSERTION"
        component = {"type": "library", "bom-ref": refs[pid], "name": p["name"], "version": p["version"], "purl": refs[pid], "licenses": [{"expression": expression}], "properties": [{"name": "mont:resolved-features", "value": ",".join(sorted(nodes[pid]["features"]))}]}
        if not local:
            key = (p["name"], p["version"], p["source"])
            require(key in checksums, "dependency is absent from Cargo.lock")
            checksum = checksums[key]
            root = Path(p["manifest_path"]).parent
            if p["source"] == "registry+https://github.com/rust-lang/crates.io-index":
                require(isinstance(checksum, str) and HEX.fullmatch(checksum), "missing registry checksum")
                component["hashes"] = [{"alg": "SHA-256", "content": checksum}]
            else:
                source = re.fullmatch(r"git\+(https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\?rev=([a-f0-9]{40})#\2", p["source"])
                require(source and checksum is None, "only immutable public GitHub revisions or locked crates.io dependencies are supported")
                revision = source[2]
                require(run(["git", "rev-parse", "HEAD"], cwd=root).strip() == revision, "Git dependency revision differs from Cargo.lock")
                changes = run(["git", "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"], cwd=root).splitlines()
                require(all(line == "?? .cargo-ok" for line in changes), "Git dependency differs from locked upstream source")
                component["properties"].append({"name": "mont:git-revision", "value": revision})
                component["externalReferences"] = [{"type": "vcs", "url": f"{source[1]}/tree/{revision}"}]
            selected = selected_component_license(component)
            if not selected:
                unresolved.append(f"{p['name']}@{p['version']}: unreviewed license {expression}")
            else:
                component["properties"].append({"name": "mont:selected-license", "value": selected})

            files = legal_files(root)
            if p.get("license_file"):
                declared = (root / p["license_file"]).resolve()
                require(declared.is_relative_to(root.resolve()) and declared.is_file(), "invalid crate license_file")
                files = sorted(set(files + [declared]))
            texts = [(f.relative_to(root).as_posix(), f.read_text(encoding="utf-8")) for f in files]
            if expression in {"MPL-2.0", "MPL-2.0+", "BSD-3-Clause/MIT"}:
                reviewed = audited_declared_notice(p, root, checksum, sources)
                require(selected and reviewed, f"unreviewed published source: {p['name']}")
                texts.extend(reviewed)
                if selected == "MPL-2.0":
                    record = next(p for p in read_json(CLI / "tool/native-notices.json")["packages"] if (p["name"], p["version"]) == (component["name"], component["version"]))
                    component["properties"].extend([
                        {"name": "mont:source-archive", "value": f"third-party-sources/{p['name']}-{p['version']}.crate"},
                        {"name": "mont:source-provenance", "value": "Unmodified upstream .crate archive; published checksum verified."},
                        {"name": "mont:cargo-vcs-revision", "value": record["cargoVcsRevision"] or "not present in published crate"},
                    ])
                    component["externalReferences"] = [{"type": "distribution", "url": record["source"]}]
            elif not texts:
                texts = audited_declared_notice(p, root, checksum) or upstream_license(p, root, upstream_cache)
            notices.append(f"\n{'=' * 72}\n{p['name']} {p['version']}\nDeclared license: {expression}\nSelected distribution license: {selected or 'UNREVIEWED'}\n")
            for name, text in texts:
                notices.append(f"\n--- {name} ---\n{text}\n")
        components.append(component)
        dependencies.append({"ref": refs[pid], "dependsOn": sorted({refs[d["pkg"]] for d in nodes[pid]["deps"] if d["pkg"] in closure and any(k["kind"] is None for k in d["dep_kinds"])})})
    font_components, font_notices = embedded_font_notices(binary)
    components.extend(font_components)
    notices.extend(font_notices)
    for font in font_components:
        dependencies.append({"ref": font["bom-ref"], "dependsOn": []})
        next(d for d in dependencies if d["ref"] == refs[root_id])["dependsOn"].append(font["bom-ref"])
    require(not public or not unresolved, "unreviewed dependency licenses block public packaging")
    sysroot = Path(run(["rustc", RUST, "--print", "sysroot"]).strip())
    rustdoc = sysroot / "share/doc/rust"
    copyright_file = rustdoc / "COPYRIGHT-library.html"
    require(copyright_file.is_file(), "install Rust standard-library copyright materials")
    copyright_html = copyright_file.read_text(encoding="utf-8")
    copyright_text = html.unescape(re.sub(r"<[^>]+>", "", copyright_html))
    notices.append("\nRust 1.91.0 standard-library runtime (not the compiler/toolchain):\nDeclared license: MIT OR Apache-2.0\nSelected distribution license: MIT (component-specific notices retained below)\n" + copyright_text)
    for name in ("MIT", "Apache-2.0", "LLVM-exception", "BSD-2-Clause", "Unicode-3.0"):
        path = rustdoc / "licenses" / f"{name}.txt"
        require(path.is_file(), "missing Rust runtime license text")
        notices.append(f"\n--- Rust {name} ---\n{path.read_text(encoding='utf-8')}\n")
    components.append({"type": "library", "bom-ref": "rust-stdlib-1.91.0", "name": "Rust standard library", "version": "1.91.0", "licenses": [{"expression": "MIT OR Apache-2.0"}], "properties": [{"name": "mont:complete-runtime-notices", "value": "THIRD_PARTY_NOTICES.txt"}, {"name": "mont:selected-license", "value": "MIT"}]})
    dependencies.append({"ref": "rust-stdlib-1.91.0", "dependsOn": []})
    next(d for d in dependencies if d["ref"] == refs[root_id])["dependsOn"].append("rust-stdlib-1.91.0")
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1, "metadata": {"properties": [{"name": "mont:target", "value": target}, {"name": "mont:scope", "value": "CLI normal dependency closure including generated-code/proc-macro dependencies and Rust runtime; exact embedded font bytes; excludes dev/build-only tools and external subprocess/runtime executables"}]}, "components": components, "dependencies": dependencies, "properties": [{"name": "mont:unreviewed-licenses", "value": json.dumps(unresolved)}]}
    return encode(sbom), "".join(notices).encode(), unresolved


def verify_bundled_sources(root, components, files):
    """Require exactly the unchanged, reviewed MPL sources named by all SBOMs."""
    expected = {}
    for component in components:
        if component.get("licenses") not in ([{"expression": "MPL-2.0"}], [{"expression": "MPL-2.0+"}]):
            continue
        require(selected_component_license(component) == "MPL-2.0", "unreviewed MPL source component")
        name = f"third-party-sources/{component['name']}-{component['version']}.crate"
        safe_path(name)
        checksum = component["hashes"][0]["content"]
        require(name not in expected or expected[name] == checksum, "conflicting MPL source digests")
        expected[name] = checksum
        record = next(p for p in read_json(CLI / "tool/native-notices.json")["packages"] if (p["name"], p["version"]) == (component["name"], component["version"]))
        for key, value in (
            ("mont:source-archive", name),
            ("mont:source-provenance", "Unmodified upstream .crate archive; published checksum verified."),
            ("mont:cargo-vcs-revision", record["cargoVcsRevision"] or "not present in published crate"),
        ):
            require([p.get("value") for p in component.get("properties", []) if p.get("name") == key] == [value], "missing/incorrect MPL source provenance")
        require(component.get("externalReferences") == [{"type": "distribution", "url": record["source"]}], "incorrect MPL source location")
        require(name in files and files[name] == checksum and (root / name).is_file() and not (root / name).is_symlink() and digest(root / name) == checksum, "missing/altered MPL source archive")
        notices = (root / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
        require(f"Bundled source: {name}" in notices and f"Source: {record['source']}" in notices, "missing MPL source notice")
    require({name for name in files if name.startswith("third-party-sources/")} == set(expected), "unexpected third-party source archive")

def payload_hashes(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def package(args):
    try:
        with signing.credentials(args, args.target or native_target()) as prepared:
            return _package(prepared)
    except signing.SigningError as error:
        raise ReleaseError(str(error)) from None


def _package(args):
    target = args.target or native_target()
    binary_name = executable_path(target)
    launcher_name = executable_path(target, "mont-mcp")
    require(target == native_target(), "packaging requires the target's native host/architecture")
    public = not args.unsigned
    signing_key = getattr(args, "signing_key", None)
    openpgp = target != TARGET
    require(not public or not openpgp or signing_key, "public Linux/Windows packaging requires --signing-key")
    require(not signing_key or public and openpgp, "--signing-key requires public Linux/Windows packaging")
    require(not signing_key or isinstance(signing_key, str) and FINGERPRINT.fullmatch(signing_key), "--signing-key must be an uppercase full primary fingerprint")
    version = args.version
    require(VERSION.fullmatch(version), "version must be 0.MINOR.PATCH-preview.N")
    require(tomllib.loads((CLI / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"] == version, "Cargo package version differs from artifact version")
    binary = Path(args.binary)
    require(binary.is_absolute(), "--binary must be absolute")
    launcher = Path(args.mcp_binary)
    require(launcher.is_absolute(), "--mcp-binary must be absolute")
    launcher_version = tomllib.loads((MCP / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
    output = Path(args.output).resolve()
    require(not output.exists() or not any(output.iterdir()), "output must be a new or empty directory")
    require(public or not any((args.identity, args.notary_profile, args.terms, args.keychain, signing_key)), "unsigned mode cannot accept signing/approved terms flags")
    require(not public or args.terms and (signing_key if openpgp else args.identity and args.notary_profile), "public packaging requires approved terms and platform signing credentials")
    require(not openpgp or not any((args.identity, args.notary_profile, args.keychain)), "Linux/Windows packaging cannot accept Apple signing flags")
    require(not args.keychain or Path(args.keychain).is_absolute() and Path(args.keychain).is_file(), "--keychain must name an existing absolute keychain")
    keychain = ["--keychain", args.keychain] if args.keychain else []
    terms = INTERNAL_TERMS.encode()
    if public:
        terms = Path(args.terms).read_bytes()
        approved = os.environ.get("MONT_CLI_PREVIEW_TERMS_SHA256", "")
        require(HEX.fullmatch(approved) and hashlib.sha256(terms).hexdigest() == approved and len(terms.strip()) > 0, "approved preview terms SHA-256 attestation required")
        if target == TARGET:
            require(args.identity.startswith("Developer ID Application: ") and "\n" not in args.identity, "a Developer ID Application identity is required")
            identities = run(["security", "find-identity", "-v", "-p", "codesigning"] + ([args.keychain] if args.keychain else []))
            require(f'"{args.identity}"' in identities, "Developer ID identity is not available in the keychain")
    libraries = check_binary(binary, target=target, execute=True, version=version)
    launcher_libraries = check_binary(launcher, target=target, execute=True, version=launcher_version, program="mont-mcp")
    sources = {}
    sbom, notices, unresolved = dependency_notices(public, binary, target, sources=sources)
    launcher_sbom, launcher_notices, launcher_unresolved = dependency_notices(public, launcher, target, MCP, sources=sources, features=("launcher",))
    notices += b"\nStandalone MCP launcher dependency notices:\n" + launcher_notices
    unresolved.extend(launcher_unresolved)
    editor_dir = Path(getattr(args, "editor_dir", None) or CLI.parent / "mont-editor/build/web-cli")
    assets = editor_files(editor_dir)
    check_editor_provenance(editor_dir)
    try:
        flutter_notices = gzip.decompress((editor_dir / "assets/NOTICES").read_bytes())
    except gzip.BadGzipFile:
        flutter_notices = (editor_dir / "assets/NOTICES").read_bytes()
    require(flutter_notices.strip(), "missing Flutter runtime notices")
    notices += b"\nFlutter web runtime and Dart package notices (assets/NOTICES):\n" + flutter_notices
    editor_sboms = {}
    for crate_name in ("web_renderer", "editor_runtime_web"):
        wasm_sbom, wasm_notices, wasm_unresolved = dependency_notices(
            public, binary, "wasm32-unknown-unknown", CLI.parent / "mont-sdk/web" / crate_name, sources=sources)
        editor_sboms[crate_name + "-SBOM.json"] = wasm_sbom
        notices += b"\nEditor WASM runtime notices:\n" + wasm_notices
        unresolved.extend(wasm_unresolved)
    cli_sbom = json.loads(sbom)
    cli_sbom.setdefault("metadata", {}).setdefault("properties", []).append({
        "name": "mont:editor-audit",
        "value": "Editor WASM normal dependency closures: editor/web_renderer-SBOM.json and editor/editor_runtime_web-SBOM.json; Flutter/Dart runtime notices: editor/assets/NOTICES; vendor licenses retained under editor/vendor",
    })
    sbom = encode(cli_sbom)
    source_commit = run(["git", "rev-parse", "HEAD"], cwd=CLI).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit), "invalid source commit")
    source_dirty = bool(run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=CLI).strip())
    require(not public or not source_dirty, "public packaging requires clean committed source")
    root_name = f"mont-{version}-{TARGETS[target][0]}" + ("" if public else "-unsigned-internal")
    archive_name = root_name + ".tar.gz"
    with tempfile.TemporaryDirectory(prefix="mont-release-") as temp:
        stage = Path(temp)
        root = stage / root_name
        (root / "bin").mkdir(parents=True)
        packaged_binary = root / binary_name
        packaged_launcher = root / launcher_name
        packaged_executables = (packaged_binary, packaged_launcher)
        for source, destination in ((binary, packaged_binary), (launcher, packaged_launcher)):
            shutil.copyfile(source, destination)
            destination.chmod(0o755)
        shutil.copyfile(CLI / "README.md", root / "README.md")
        (root / "example").mkdir()
        for name in ("workflow.mont.json", "video-workflow.mont.json", "vertical.inputs.json"):
            example = CLI / "example" / name
            require(example.is_file() and not example.is_symlink(), "runnable workflow example missing")
            shutil.copyfile(example, root / "example" / name)
        for asset in assets:
            destination = root / "editor" / asset.relative_to(editor_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset, destination)
        for name, content in editor_sboms.items():
            (root / "editor" / name).write_bytes(content)
        (root / "SBOM.json").write_bytes(sbom)
        (root / "MCP-SBOM.json").write_bytes(launcher_sbom)
        (root / "THIRD_PARTY_NOTICES.txt").write_bytes(notices)
        (root / "TERMS.txt").write_bytes(terms)
        for name, archive in sources.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(archive, destination)
        components = cli_sbom["components"] + json.loads(launcher_sbom)["components"] + [c for content in editor_sboms.values() for c in json.loads(content)["components"]]
        verify_bundled_sources(root, components, payload_hashes(root))
        for path in root.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                require(not PRIVATE_PATH.search(data), f"private filesystem path in release payload: {path.relative_to(root)}")
                if path not in packaged_executables:
                    # Notices can discuss other implementations; only executable and
                    # resolved dependency names decide the forbidden-library gate.
                    require(not path.is_symlink(), "symlink in release payload")
        signing_info = {"state": "unsigned", "adHoc": target == TARGET}
        if public and openpgp:
            signing_info = {"state": "openpgp-signed", "fingerprint": signing_key}
        elif public:
            for executable in packaged_executables:
                run(["codesign", "--force", "--sign", getattr(args, "identity_sha1", args.identity), "--options", "runtime", "--timestamp", *keychain, executable])
                identity = signature(executable)
                require(identity["identity"] == args.identity, "unexpected signing identity")
                if executable == packaged_binary:
                    signing_info = {"state": "developer-id-notarized", **identity}
                else:
                    require(identity == {key: signing_info[key] for key in ("identity", "teamId")}, "launcher signing identity differs")
        elif target == TARGET:
            # ARM64 requires an ad-hoc code seal to execute. This is NOT a
            # Developer ID signature, notarization or redistribution approval.
            for executable in packaged_executables:
                run(["codesign", "--force", "--sign", "-", executable])
        files = payload_hashes(root)
        validate_payload_paths(files, target)
        manifest = {"schemaVersion": 1, "version": version, "runtimeVersion": version, "target": target, "root": root_name, "archive": archive_name, "sourceCommit": source_commit, "sourceDirty": source_dirty, "binarySha256": files[binary_name], "distribution": "public" if public else "internal-only", "signing": signing_info, "termsSha256": files["TERMS.txt"], "systemLibraries": libraries, "runtimeExecutables": RUNTIME_EXECUTABLES, "unreviewedLicenses": unresolved, "files": files}
        manifest.update(launcherVersion=launcher_version, launcherSha256=files[launcher_name], launcherSystemLibraries=launcher_libraries)
        # The notarized submission includes the same immutable payload as the tar.
        if public and target == TARGET:
            submission = stage / "notarization.zip"
            run(["ditto", "-c", "-k", "--keepParent", root, submission])
            result = json.loads(run(["xcrun", "notarytool", "submit", submission, "--keychain-profile", args.notary_profile, *keychain, "--wait", "--output-format", "json"]))
            require(result.get("status") == "Accepted" and re.fullmatch(r"[0-9a-fA-F-]{36}", result.get("id", "")), "Apple notarization was not Accepted")
            signing_info["notarization"] = {"id": result["id"], "status": "Accepted", "submittedArchiveSha256": digest(submission)}
            for executable in packaged_executables:
                assess(executable)
            require(payload_hashes(root) == files, "notarization changed immutable payload")

        if public:
            require(run(["git", "rev-parse", "HEAD"], cwd=CLI).strip() == source_commit and not run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=CLI).strip(), "source changed during packaging")
        output.mkdir(parents=True, exist_ok=True)
        archive = stage / archive_name
        with tarfile.open(archive, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
            for path in [root, *sorted(root.rglob("*"))]:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(stage).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode = 0o755 if path.is_dir() or path in packaged_executables else 0o644
                if path.is_file():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)
        manifest["archiveSha256"] = digest(archive)
        manifest_path = stage / "release.json"
        manifest_path.write_bytes(encode(manifest))
        sums = f"{manifest['archiveSha256']}  {archive_name}\n{digest(manifest_path)}  release.json\n"
        (stage / "SHA256SUMS").write_text(sums, encoding="utf-8", newline="\n")
        if public and openpgp:
            signing.detached_openpgp(stage / "SHA256SUMS", stage / "SHA256SUMS.asc", signing_key)
            verify_openpgp_signature(stage / "SHA256SUMS.asc", stage / "SHA256SUMS", signing_key, target=target)
        for path in (archive, manifest_path, stage / "SHA256SUMS"):
            shutil.copyfile(path, output / path.name)
        if public and openpgp:
            shutil.copyfile(stage / "SHA256SUMS.asc", output / "SHA256SUMS.asc")
    print(json.dumps({"manifest": manifest, "output": str(output)}, sort_keys=True))


def validate_manifest(manifest, archive_name, require_signed):
    require(isinstance(manifest, dict) and manifest.get("schemaVersion") == 1, "unsupported release manifest")
    version = manifest.get("version", "")
    require(isinstance(version, str) and VERSION.fullmatch(version) and manifest.get("runtimeVersion") == version, "invalid release/runtime version")
    target = manifest.get("target")
    binary_name = executable_path(target)
    launcher_name = executable_path(target, "mont-mcp")
    require(isinstance(manifest.get("launcherVersion"), str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", manifest["launcherVersion"]), "invalid launcher version")
    require(isinstance(manifest.get("launcherSystemLibraries"), list) and all(isinstance(name, str) for name in manifest["launcherSystemLibraries"]), "invalid launcher system libraries")
    require(manifest.get("runtimeExecutables") == RUNTIME_EXECUTABLES, "unexpected/incomplete external runtime executables")
    signing = manifest.get("signing", {})
    require(isinstance(signing, dict), "invalid signing metadata")
    apple_public = target == TARGET and signing.get("state") == "developer-id-notarized"
    openpgp_public = target != TARGET and signing.get("state") == "openpgp-signed"
    if openpgp_public:
        require(set(signing) == {"state", "fingerprint"} and isinstance(signing["fingerprint"], str) and FINGERPRINT.fullmatch(signing["fingerprint"]), "invalid OpenPGP signing metadata")
    public = apple_public or openpgp_public
    require(public or type(signing.get("adHoc")) is bool and signing == {"state": "unsigned", "adHoc": target == TARGET}, "invalid signing state")
    require(not require_signed or public, "unsigned internal archives cannot be published or installed through public channels")
    require(manifest.get("distribution") == ("public" if public else "internal-only"), "distribution/signature mismatch")
    expected_root = f"mont-{version}-{TARGETS[target][0]}" + ("" if public else "-unsigned-internal")
    require(manifest.get("root") == expected_root and manifest.get("archive") == expected_root + ".tar.gz" == archive_name, "archive/root/version mismatch")
    require(isinstance(manifest.get("sourceCommit"), str) and re.fullmatch(r"[0-9a-f]{40}", manifest["sourceCommit"]), "invalid source commit")
    require(isinstance(manifest.get("sourceDirty"), bool) and (not public or not manifest["sourceDirty"]), "public releases require clean source provenance")
    for key in ("archiveSha256", "binarySha256", "launcherSha256", "termsSha256"):
        require(isinstance(manifest.get(key), str) and HEX.fullmatch(manifest[key]), "invalid release digest")
    files = manifest.get("files")
    require(isinstance(files, dict), "invalid release files")
    validate_payload_paths(files, target)
    for path, checksum in files.items():
        safe_path(path)
        require(isinstance(checksum, str) and HEX.fullmatch(checksum), "invalid payload digest")
    require(files[binary_name] == manifest["binarySha256"] and files["TERMS.txt"] == manifest["termsSha256"], "inconsistent manifest digests")
    require(files[launcher_name] == manifest["launcherSha256"], "inconsistent launcher digest")
    if public:
        if apple_public:
            notary = signing.get("notarization", {})
            require(isinstance(notary, dict), "invalid notarization metadata")
            require(notary.get("status") == "Accepted" and isinstance(notary.get("id"), str) and re.fullmatch(r"[0-9a-fA-F-]{36}", notary["id"]) and isinstance(notary.get("submittedArchiveSha256"), str) and HEX.fullmatch(notary["submittedArchiveSha256"]), "missing notarization evidence")
        require(manifest.get("unreviewedLicenses") == [], "unreviewed licenses block public release")
    return public


def extract_archive(archive, dest, manifest):
    """Manual regular-file extraction: no links, devices, pax paths or overwrite."""
    root = manifest["root"]
    expected_files = {f"{root}/{p}": h for p, h in manifest["files"].items()}
    expected_dirs = {parent.as_posix() for name in expected_files for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    seen = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            safe_path(member.name)
            require(member.name not in seen, "duplicate archive path")
            seen.add(member.name)
            require(not member.pax_headers and not member.linkname and member.uid == member.gid == 0, "unexpected archive metadata")
            require(member.isdir() or member.isfile(), "only directories and regular files are allowed")
            if member.isdir():
                require(member.name in expected_dirs and member.mode == 0o755, "unexpected directory")
                continue
            require(member.name in expected_files and 0 <= member.size <= 512 * 1024 * 1024, "unexpected/oversized archive file")
            mode = 0o755 if member.name in {f"{root}/{path}" for path in executable_paths(manifest["target"])} else 0o644
            require(member.mode == mode, "unexpected payload file permissions")
            target = dest.joinpath(*PurePosixPath(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            require(source is not None, "unreadable archive file")
            with source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
            require(digest(target) == expected_files[member.name], "payload checksum mismatch")
            target.chmod(mode)
    require(seen == set(expected_files) | expected_dirs, "incomplete archive")
    return dest / root


def verify_archive_integrity(archive, manifest):
    """Authenticate sidecars and hashes; does not assess native executables."""
    archive = Path(archive)
    manifest_path = archive.parent / "release.json"
    sums = archive.parent / "SHA256SUMS"
    for path in (archive, manifest_path, sums):
        require(path.is_file() and not path.is_symlink(), "regular archive and release sidecars required")
    public = validate_manifest(manifest, archive.name, False)
    if public and manifest["target"] != TARGET:
        verify_openpgp_signature(archive.parent / "SHA256SUMS.asc", sums, manifest["signing"]["fingerprint"], target=manifest["target"])
    expected = f"{manifest['archiveSha256']}  {archive.name}\n{digest(manifest_path)}  release.json\n"
    require(sums.read_text(encoding="utf-8") == expected, "release manifest/archive checksum list mismatch")
    require(digest(archive) == manifest["archiveSha256"], "archive checksum mismatch")


def verify(args):
    """Verify complete contents; portable skips only host-specific acceptance."""
    archive = Path(args.archive).absolute()
    manifest_path = archive.parent / "release.json"
    require(not archive.is_symlink() and archive.is_file(), "archive must be a regular file")
    require(manifest_path.stat().st_size < 1024 * 1024, "release manifest too large")
    manifest = read_json(manifest_path)
    public = validate_manifest(manifest, archive.name, args.require_signed)
    target = manifest["target"]
    portable = getattr(args, "portable", False)
    require(portable or target == native_target(), "verification requires the target's native host/architecture")
    verify_archive_integrity(archive, manifest)
    destination = Path(args.extract).absolute() if args.extract else None
    require(destination is None or not destination.exists(), "extraction destination must not exist")
    with tempfile.TemporaryDirectory(prefix="mont-verify-", dir=destination.parent if destination else None) as temp:
        root = extract_archive(archive, Path(temp), manifest)
        binary = root / executable_path(target)
        libraries = check_portable_binary(binary, target) if portable else check_binary(binary, target=target)
        require(libraries is None or libraries == manifest.get("systemLibraries"), "dynamic dependencies differ from manifest")
        launcher = root / executable_path(target, "mont-mcp")
        launcher_libraries = check_portable_binary(launcher, target) if portable else check_binary(launcher, target=target)
        require(launcher_libraries is None or launcher_libraries == manifest["launcherSystemLibraries"], "launcher dynamic dependencies differ from manifest")
        if public:
            if target == TARGET and not portable:
                for executable in (binary, launcher):
                    signature(executable, manifest["signing"])
                    assess(executable)
        else:
            require((root / "TERMS.txt").read_bytes() == INTERNAL_TERMS.encode(), "internal terms altered")
            if target == TARGET and not portable:
                for executable in (binary, launcher):
                    run(["codesign", "--verify", "--strict", executable])
                    details = run(["codesign", "-d", "--verbose=4", executable])
                    require("Signature=adhoc" in details and "Authority=" not in details, "internal archive must have only an ad-hoc code seal")
        sbom = read_json(root / "SBOM.json")
        require(sbom.get("bomFormat") == "CycloneDX" and sbom.get("specVersion") == "1.6" and sbom.get("components"), "invalid SBOM")
        require([p.get("value") for p in sbom.get("metadata", {}).get("properties", []) if p.get("name") == "mont:target"] == [target], "SBOM target differs from release manifest")
        font_components, _ = embedded_font_notices(binary)
        require([c for c in sbom["components"] if c.get("bom-ref", "").startswith("mont:embedded-font:")] == font_components, "embedded font SBOM differs from audited executable")
        launcher_sbom = read_json(root / "MCP-SBOM.json")
        require(launcher_sbom.get("bomFormat") == "CycloneDX" and launcher_sbom.get("specVersion") == "1.6" and launcher_sbom.get("components"), "invalid launcher SBOM")
        require([p.get("value") for p in launcher_sbom.get("metadata", {}).get("properties", []) if p.get("name") == "mont:target"] == [target], "launcher SBOM target differs")
        launcher_fonts, _ = embedded_font_notices(launcher)
        require([c for c in launcher_sbom["components"] if c.get("bom-ref", "").startswith("mont:embedded-font:")] == launcher_fonts, "launcher embedded font SBOM differs from audited executable")
        font_components.extend(launcher_fonts)
        sbom["components"].extend(launcher_sbom["components"])
        for name in sorted(EDITOR_SBOMS):
            if "editor/" + name not in manifest["files"]:
                continue
            editor_sbom = read_json(root / "editor" / name)
            require(editor_sbom.get("bomFormat") == "CycloneDX" and editor_sbom.get("specVersion") == "1.6" and editor_sbom.get("components"), "invalid editor SBOM")
            require([p.get("value") for p in editor_sbom.get("metadata", {}).get("properties", []) if p.get("name") == "mont:target"] == ["wasm32-unknown-unknown"], "editor SBOM target differs")
            sbom["components"].extend(editor_sbom["components"])
        verify_bundled_sources(root, sbom["components"], manifest["files"])
        for component in sbom["components"]:
            require(not FORBIDDEN.search(component["name"].encode()), "forbidden SBOM dependency")
            if public:
                expressions = component.get("licenses", [])
                require(len(expressions) == 1, "ambiguous component license")
                expression = expressions[0].get("expression", "")
                selected = selected_component_license(component)
                require(selected or expression == "LicenseRef-Mont-Proprietary" or component in font_components, "unreviewed SBOM license")
                if selected:
                    require([p.get("value") for p in component.get("properties", []) if p.get("name") == "mont:selected-license"] == [selected], "missing/incorrect selected SBOM license")
        for path in root.rglob("*"):
            require(not path.is_file() or not PRIVATE_PATH.search(path.read_bytes()), f"private path in release payload: {path.relative_to(root)}")
        if public and not portable:
            check_binary(binary, target=target, execute=True, version=manifest["version"])
            check_binary(launcher, target=target, execute=True, version=manifest["launcherVersion"], program="mont-mcp")
        if destination:
            # Rename only after all validation; no partial caller-visible extraction.
            os.rename(temp, destination)
            root = destination / manifest["root"]
        result = {"manifest": manifest}
        if destination:
            result.update({"root": str(root), "binary": str(root / executable_path(target))})
            result["launcher"] = str(root / executable_path(target, "mont-mcp"))
        print(json.dumps(result, sort_keys=True))
        return result


def debian_control(manifest, revision):
    architectures = {"aarch64-unknown-linux-gnu": "arm64", "x86_64-unknown-linux-gnu": "amd64"}
    require(manifest["target"] in architectures, "Debian packages require a supported Linux target")
    public = manifest["distribution"] == "public"
    require(manifest["distribution"] in {"public", "internal-only"}, "invalid Debian distribution")
    require(not public or manifest.get("signing", {}).get("state") == "openpgp-signed", "public Debian packages require Linux signing")
    require(type(revision) is int and revision > 0, "Debian package revision must be a positive integer")
    require(set(manifest["systemLibraries"]) | set(manifest["launcherSystemLibraries"]) <= {"libssl.so.3", "libcrypto.so.3", "libgcc_s.so.1", "libm.so.6", "libc.so.6", "ld-linux-x86-64.so.2", "ld-linux-aarch64.so.1"}, "unmapped Debian runtime dependency")
    architecture = architectures[manifest["target"]]
    version = manifest["version"].replace("-preview.", "~preview.") + f"-{revision}"
    name = "mont" if public else "mont-cli-internal"
    description = "proprietary developer preview" if public else "internal SDK-backed video editor"
    terms = "Approved Developer Preview terms and third-party notices are included." if public else "Internal engineering candidate; no public redistribution permission."
    control = f"""Package: {name}
Version: {version}
Section: video
Priority: optional
Architecture: {architecture}
Maintainer: Nauchu AB <nauchu.info@gmail.com>
Depends: ffmpeg, libc6 (>= 2.36), libgcc-s1, libssl3 | libssl3t64, libvulkan1, mesa-vulkan-drivers
Description: Mont CLI {description}
 {terms}
"""
    return architecture, version, control


def package_debian(args):
    require(sys.platform == "linux" and shutil.which("dpkg-deb"), "Debian packaging requires Linux and dpkg-deb")
    output = Path(args.output).absolute()
    require(not output.exists(), "Debian output directory must not exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mont-debian-", dir=output.parent) as temp:
        temp = Path(temp)
        verified = verify(argparse.Namespace(archive=args.archive, require_signed=False, extract=temp / "verified"))
        manifest = verified["manifest"]
        architecture, version, control = debian_control(manifest, args.revision)
        require(run(["dpkg", "--print-architecture"]).strip() == architecture, "Debian host architecture differs from candidate")
        stage = temp / "package"
        (stage / "DEBIAN").mkdir(parents=True)
        (stage / "DEBIAN/control").write_text(control, encoding="utf-8", newline="\n")
        root = Path(verified["root"])
        package_name = "mont" if manifest["distribution"] == "public" else "mont-cli-internal"
        data_root = stage / "usr/share/mont"
        for name in manifest["files"]:
            destination = stage / "usr" / name if name in executable_paths(manifest["target"]) else data_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, destination)
        (data_root / "release.json").write_bytes(encode(manifest))
        for path in (stage, *stage.rglob("*")):
            path.chmod(0o755 if path.is_dir() or path in {stage / "usr" / name for name in executable_paths(manifest["target"])} else 0o644)
        distribution = temp / "distribution"
        distribution.mkdir()
        binary_package = distribution / f"{package_name}_{version}_{architecture}.deb"
        run(["dpkg-deb", "--root-owner-group", "--build", stage, binary_package])
        checksum = digest(binary_package)
        (distribution / "SHA256SUMS").write_text(f"{checksum}  {binary_package.name}\n", encoding="utf-8", newline="\n")
        os.rename(distribution, output)
    print(json.dumps({"package": str(output / binary_package.name), "sha256": checksum, "sourceCommit": manifest["sourceCommit"], "distribution": manifest["distribution"]}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("package", help="package an existing release build")
    pack.add_argument("--binary", required=True)
    pack.add_argument("--mcp-binary", required=True, help="standalone mont-mcp build with --features launcher")
    pack.add_argument("--editor-dir", help="built Flutter web assets (default: projects/mont-editor/build/web-cli)")
    pack.add_argument("--output", required=True)
    pack.add_argument("--version", required=True)
    pack.add_argument("--target", choices=TARGETS, help="native host/architecture by default")
    pack.add_argument("--unsigned", action="store_true", help="internal-only; no redistribution permission")
    pack.add_argument("--signing-key", help="Linux/Windows public release: uppercase full OpenPGP primary fingerprint; private key via GNUPGHOME")
    pack.add_argument("--identity", help="explicit Developer ID; default: restore the pinned local signing configuration")
    pack.add_argument("--notary-profile")
    pack.add_argument("--keychain", help="isolated signing identity and notary-profile keychain (absolute path)")
    pack.add_argument("--terms", help="approved terms file; requires MONT_CLI_PREVIEW_TERMS_SHA256")
    check = commands.add_parser("verify", help="verify an archive and adjacent release.json/SHA256SUMS")
    check.add_argument("--archive", required=True)
    check.add_argument("--require-signed", action="store_true")
    check.add_argument("--extract", help="new destination directory, published only on success")
    debian = commands.add_parser("debian", help="build a Debian package from a verified Linux archive")
    debian.add_argument("--archive", required=True)
    debian.add_argument("--output", required=True, help="new output directory")
    debian.add_argument("--revision", type=int, default=1, help="Debian packaging revision; does not change the CLI version")
    args = parser.parse_args()
    try:
        {"package": package, "verify": verify, "debian": package_debian}[args.command](args)
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        # Exception detail may contain private paths or native-tool output.
        message = str(error) if isinstance(error, ReleaseError) else type(error).__name__
        print(f"release: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
