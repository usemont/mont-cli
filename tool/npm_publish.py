#!/usr/bin/env python3
"""Pack verified native packages, publish a candidate, then explicitly promote it."""
import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen

import npm_package
import release


REGISTRY = "https://registry.npmjs.org/"
ROOT = npm_package.PACKAGE
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(release.encode(data))
    temporary.replace(path)


def integrity(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def registry_url(value):
    parsed = urlparse(value)
    require(parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment,
            "registry must be an HTTP(S) URL without embedded credentials")
    require(parsed.scheme == "https" or parsed.hostname in ("localhost", "127.0.0.1", "::1"),
            "plain HTTP registries are only permitted on loopback")
    return value.rstrip("/") + "/"


def npm(arguments, registry=None, *, cwd=None, absent=False):
    command = ["npm", *map(str, arguments), "--ignore-scripts", "--json"]
    if registry:
        # The explicit CLI flag takes precedence over publishConfig.registry;
        # scope configuration must not silently redirect @usemont elsewhere.
        command += ["--registry", registry, "--@usemont:registry=" + registry]
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = None
    if result.returncode:
        if absent and isinstance(data, dict) and data.get("error", {}).get("code") == "E404":
            return None
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return data


def files(directory):
    result = {}
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "package sets cannot contain symlinks")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = path
    return result


def package_set(output):
    packages = Path(output) / "packages"
    expected = {"cli", *("cli-" + target[0] for target in npm_package.TARGETS.values())}
    require(packages.is_dir() and not packages.is_symlink()
            and {p.name for p in packages.iterdir()} == expected, "complete five-package set required")
    root = read_json(packages / "cli/package.json")
    version = root.get("version")
    require(isinstance(version, str) and VERSION.fullmatch(version), "invalid exact package version")
    dependencies = {ROOT + "-" + target[0]: version for target in npm_package.TARGETS.values()}
    require(root.get("name") == ROOT and root.get("optionalDependencies") == dependencies,
            "root must pin all four native payloads to its exact version")
    identity = None
    entries = []
    for directory in sorted(expected):
        path = packages / directory
        require(path.is_dir() and not path.is_symlink(), "regular package directories required")
        package = read_json(path / "package.json")
        require(package.get("name") == "@usemont/" + directory and package.get("version") == version,
                "package name/version differs from the complete package set")
        require(not package.get("private") and not package.get("scripts") and not package.get("dependencies"),
                "private packages, lifecycle scripts and additional dependencies cannot be published")
        require(package.get("publishConfig") == {"access": "public", "registry": REGISTRY},
                "public npm publish configuration required")
        contents = files(path)
        require("TERMS.txt" in contents, "packaged terms required")
        terms = hashlib.sha256(contents["TERMS.txt"].read_bytes()).hexdigest()
        if directory != "cli":
            target, (suffix, operating_system, cpu) = next((t, info) for t, info in npm_package.TARGETS.items()
                if "cli-" + info[0] == directory)
            require(package.get("os") == [operating_system] and package.get("cpu") == [cpu]
                    and (operating_system != "linux" or package.get("libc") == ["glibc"]),
                    "native package platform selectors differ from target")
            manifest = read_json(path / "release.json")
            require(manifest.get("version") == version and manifest.get("target") == target
                    and manifest.get("termsSha256") == terms, "native release identity or terms mismatch")
            current = {key: manifest[key] for key in ("version", "sourceCommit", "sourceDirty", "termsSha256", "launcherVersion")}
            require(current["sourceDirty"] is False, "dirty-source packages cannot be published")
            require(identity is None or current == identity, "native package provenance differs")
            identity = current
            declared = manifest.get("files")
            require(isinstance(declared, dict) and set(contents) == set(declared) | {"package.json", "release.json"},
                    "native package files differ from verified release manifest")
            for name, digest in declared.items():
                require(name in contents and hashlib.sha256(contents[name].read_bytes()).hexdigest() == digest,
                        "native payload content has been tampered with")
        entries.append({"name": package["name"], "version": version, "directory": "packages/" + directory})
    require(hashlib.sha256((packages / "cli/TERMS.txt").read_bytes()).hexdigest() == identity["termsSha256"],
            "root and native terms differ")
    return identity, entries


def local_path(output, value, prefix):
    require(isinstance(value, str) and not Path(value).is_absolute() and ".." not in Path(value).parts
            and value.startswith(prefix + "/"), "unsafe package-set path")
    root = Path(output).resolve()
    result = root / value
    require(result.resolve().is_relative_to(root) and result.is_file() and not result.is_symlink(),
            "package-set artifact must be a contained regular file")
    return result


def verify_pack(directory, tarball):
    expected = files(directory)
    with tarfile.open(tarball, "r:gz") as archive:
        packed = {}
        for member in archive.getmembers():
            if member.isdir():
                continue
            require(member.isfile() and member.name.startswith("package/")
                    and member.name not in packed, "unsafe or duplicate npm tarball member")
            name = member.name[len("package/"):]
            require(name in expected and name not in packed, "npm tarball contains unexpected files")
            packed[name] = True
            require(archive.extractfile(member).read() == expected[name].read_bytes(),
                    "npm tarball differs from staged package content")
            require(os.name == "nt" or member.mode & 0o111 == expected[name].stat().st_mode & 0o111,
                    "npm tarball executable permissions differ from staged package")
        require(set(packed) == set(expected), "npm tarball omits staged package files")


def pack(output):
    """Pack once; retries validate and reuse the original immutable tarballs."""
    output = Path(output).absolute()
    identity, entries = package_set(output)
    index_path = output / "index.json"
    if index_path.exists():
        index = read_json(index_path)
        require(index.get("schemaVersion") == 1 and all(index.get(key) == value for key, value in identity.items()),
                "package index identity mismatch")
        indexed = index.get("packages")
        require(isinstance(indexed, list) and len(indexed) == len(entries)
                and {p.get("name") for p in indexed} == {p["name"] for p in entries}, "incomplete package index")
        for entry in entries:
            artifact = next(p for p in indexed if p["name"] == entry["name"])
            require(all(artifact.get(key) == value for key, value in entry.items()), "package index metadata mismatch")
            tarball = local_path(output, artifact.get("tarball"), "tarballs")
            require(type(artifact.get("size")) is int and artifact["size"] == tarball.stat().st_size,
                    "packed tarball size mismatch")
            require(integrity(tarball.read_bytes()) == artifact.get("integrity"), "packed tarball integrity mismatch")
            verify_pack(output / entry["directory"], tarball)
        return index
    tarballs = output / "tarballs"
    tarballs.mkdir(exist_ok=True)
    for entry in entries:
        result = npm(["pack", output / entry["directory"], "--pack-destination", tarballs], cwd=output)
        require(isinstance(result, list) and len(result) == 1, "npm pack returned malformed metadata")
        filename = result[0]["filename"]
        require(Path(filename).name == filename, "npm pack returned unsafe filename")
        entry["tarball"] = "tarballs/" + filename
        tarball = local_path(output, entry["tarball"], "tarballs")
        entry["size"] = tarball.stat().st_size
        entry["integrity"] = integrity(tarball.read_bytes())
        require(result[0].get("integrity") == entry["integrity"], "npm pack integrity differs from tarball")
        verify_pack(output / entry["directory"], tarball)
    index = {"schemaVersion": 1, **identity, "packages": entries}
    write_json(index_path, index)
    return index


def remote(entry, registry, *, absent=False, tag=None):
    metadata = npm(["view", entry["name"] + "@" + entry["version"]], registry, absent=absent)
    if metadata is None:
        return None
    require(isinstance(metadata, dict) and metadata.get("name") == entry["name"]
            and metadata.get("version") == entry["version"], "registry returned wrong package identity")
    dist = metadata.get("dist", {})
    require(dist.get("integrity") == entry["integrity"], "immutable registry version integrity mismatch")
    url = dist.get("tarball", "")
    require(urlparse(url).scheme in ("http", "https"), "registry tarball must use HTTP(S)")
    size = entry["size"]
    require(type(size) is int and size > 0, "expected registry tarball size required")
    digest = hashlib.sha512()
    downloaded = 0
    with urlopen(url, timeout=60) as response:
        while downloaded <= size:
            chunk = response.read(min(1024 * 1024, size + 1 - downloaded))
            if not chunk:
                break
            downloaded += len(chunk)
            digest.update(chunk)
    require(downloaded == size and "sha512-" + base64.b64encode(digest.digest()).decode() == entry["integrity"],
            "registry tarball bytes fail bounded integrity verification")
    if tag:
        tags = metadata.get("dist-tags")
        require(isinstance(tags, dict) and tags.get(tag) == entry["version"], "registry candidate tag is unavailable or mismatched")
    return {"name": entry["name"], "version": entry["version"], "integrity": entry["integrity"], "tarball": url}


def candidate_tag(tag, version):
    require(tag == "candidate-" + version, "candidate tag must be candidate-VERSION, never preview or latest")


def publish_candidate(output, tag, registry=REGISTRY):
    """Publish an already verified stage; CLI additionally rechecks source archives."""
    output = Path(output).absolute()
    registry = registry_url(registry)
    index = pack(output)
    candidate_tag(tag, index["version"])
    entries = sorted(index["packages"], key=lambda entry: entry["name"] == ROOT)
    # Preflight every immutable version before any write, including the root.
    existing = {entry["name"]: remote(entry, registry, absent=True) for entry in entries}
    verified = []
    for entry in entries:
        if entry["name"] == ROOT:
            # All four dependencies must actually be downloadable with candidate
            # tags before exposing the root package, not merely PUT successfully.
            for payload in entries[:-1]:
                remote(payload, registry, tag=tag)
        if existing[entry["name"]] is None:
            npm(["publish", output / entry["tarball"], "--access", "public", "--tag", tag], registry, cwd=output)
        else:
            tags = npm(["view", entry["name"] + "@" + entry["version"], "dist-tags"], registry)
            require(isinstance(tags, dict) and tags.get(tag, entry["version"]) == entry["version"],
                    "candidate tag already refers to another immutable version")
            if tags.get(tag) != entry["version"]:
                npm(["dist-tag", "add", entry["name"] + "@" + entry["version"], tag], registry)
        verified.append(remote(entry, registry, tag=tag))
    receipt = {"schemaVersion": 1, "version": index["version"], "registry": registry,
               "candidateTag": tag, "packages": verified}
    write_json(output / "receipt.json", receipt)
    return receipt


def promote(output, candidate, channel, registry=REGISTRY):
    output = Path(output).absolute()
    registry = registry_url(registry)
    index = pack(output)
    candidate_tag(candidate, index["version"])
    require(channel in ("preview", "latest"), "promotion channel must be preview or latest")
    require(channel != "latest" or "-" not in index["version"], "latest refuses prerelease versions")
    receipt = read_json(output / "receipt.json")
    require(receipt.get("schemaVersion") == 1 and receipt.get("version") == index["version"]
            and receipt.get("candidateTag") == candidate and receipt.get("registry") == registry,
            "candidate receipt identity mismatch")
    packages = receipt.get("packages")
    require(isinstance(packages, list) and len(packages) == 5
            and {p.get("name") for p in packages} == {p["name"] for p in index["packages"]},
            "candidate receipt is incomplete")
    for entry in index["packages"]:
        actual = remote(entry, registry, tag=candidate)
        require(actual == next(p for p in packages if p["name"] == entry["name"]), "candidate receipt differs from registry")
    npm(["dist-tag", "add", ROOT + "@" + index["version"], channel], registry)
    tags = npm(["view", ROOT + "@" + index["version"], "dist-tags"], registry)
    require(isinstance(tags, dict) and tags.get(channel) == index["version"], "root channel promotion was not confirmed")
    return {"version": index["version"], "channel": channel, "registry": registry}


def stage(archives, output, approved_terms_sha256):
    """Preserve trust inputs so later publication can reverify prepared output."""
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), "npm output directory must not exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mont-publication-", dir=output.parent) as temporary:
        working = Path(temporary) / "release"
        working.mkdir()
        sources = working / "sources"
        sources.mkdir()
        preserved = []
        for original in archives:
            original = Path(original).absolute()
            manifest = read_json(original.parent / "release.json")
            target = manifest["target"]
            require(target in npm_package.TARGETS, "unsupported native archive target")
            destination = sources / target
            destination.mkdir()
            for name in (original.name, "release.json", "SHA256SUMS", "SHA256SUMS.asc"):
                source = original.parent / name
                if name == "SHA256SUMS.asc" and not source.exists():
                    continue
                require(source.is_file() and not source.is_symlink(), "regular native archive and trust sidecars required")
                shutil.copyfile(source, destination / name)
            preserved.append(destination / original.name)
        npm_package.prepare(preserved, working / "packages", approved_terms_sha256)
        write_json(working / "sources.json", {"schemaVersion": 1, "approvedTermsSha256": approved_terms_sha256,
            "archives": [p.relative_to(working).as_posix() for p in preserved]})
        pack(working)
        working.rename(output)
    return output


def verify_sources(output):
    output = Path(output).absolute()
    provenance = read_json(output / "sources.json")
    require(provenance.get("schemaVersion") == 1 and isinstance(provenance.get("archives"), list)
            and len(provenance["archives"]) == 4, "verified source archives required for publication")
    archives = [local_path(output, value, "sources") for value in provenance["archives"]]
    with tempfile.TemporaryDirectory(prefix="mont-publication-recheck-") as temporary:
        verified = Path(temporary) / "packages"
        npm_package.prepare(archives, verified, provenance["approvedTermsSha256"])
        trusted = files(verified)
        staged = files(output / "packages")
        require(trusted.keys() == staged.keys() and all(
                trusted[name].read_bytes() == staged[name].read_bytes()
                and (os.name == "nt" or trusted[name].stat().st_mode & 0o111 == staged[name].stat().st_mode & 0o111)
                for name in trusted), "prepared package content or executable permissions differ from signature-verified archives")
    pack(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "candidate"):
        command = commands.add_parser(name)
        command.add_argument("--archive", type=Path, action="append")
        command.add_argument("--output", type=Path)
        command.add_argument("--approved-terms-sha256")
        if name == "candidate":
            command.add_argument("--prepared-output", type=Path)
            command.add_argument("--tag", required=True)
            command.add_argument("--registry", default=REGISTRY)
    command = commands.add_parser("pack", help="pack an existing output/packages set without publishing")
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("promote")
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--candidate-tag", required=True)
    command.add_argument("--channel", choices=("preview", "latest"), required=True)
    command.add_argument("--registry", default=REGISTRY)
    args = parser.parse_args()
    try:
        # Keep stdout machine-readable despite native archive verification output.
        with redirect_stdout(io.StringIO()):
            if args.command == "pack":
                result = pack(args.output)
            elif args.command == "promote":
                verify_sources(args.output)
                result = promote(args.output, args.candidate_tag, args.channel, args.registry)
            elif args.command == "candidate" and args.prepared_output:
                require(not args.archive and args.output is None and args.approved_terms_sha256 is None,
                        "prepared-output cannot be combined with archive staging options")
                verify_sources(args.prepared_output)
                result = publish_candidate(args.prepared_output, args.tag, args.registry)
            else:
                require(args.archive and args.output and args.approved_terms_sha256,
                        "archive, output and independently approved terms SHA256 are required")
                result = stage(args.archive, args.output, args.approved_terms_sha256)
                if args.command == "candidate":
                    result = publish_candidate(result, args.tag, args.registry)
                else:
                    result = {"output": str(result), **read_json(result / "index.json")}
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, StopIteration, tarfile.TarError,
            release.ReleaseError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        parser.exit(1, f"npm-publish: {detail}\n")


if __name__ == "__main__":
    main()
