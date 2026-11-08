#!/usr/bin/env python3
"""Release signing: disposable pinned Developer ID and detached OpenPGP."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile


class SigningError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SigningError(message)


def run(command, *, data=None, env=None):
    # Neither command arguments nor native diagnostics may expose credentials.
    try:
        result = subprocess.run(command, input=data, capture_output=True, env=env, timeout=180)
    except subprocess.TimeoutExpired:
        raise SigningError(f"{Path(command[0]).name}: signing command timed out (diagnostics withheld)") from None
    require(result.returncode == 0, f"{Path(command[0]).name} {command[1]}: signing command failed (diagnostics withheld)")
    return result.stdout


def detached_openpgp(data_path, signature_path, fingerprint):
    """Sign with the caller's GNUPGHOME; verification supplies independent trust."""
    require(isinstance(fingerprint, str) and re.fullmatch(r"[0-9A-F]{40}", fingerprint),
            "full uppercase OpenPGP primary fingerprint required")
    run(["gpg", "--batch", "--yes", "--armor", "--digest-algo", "SHA256",
         "--local-user", fingerprint, "--output", str(signature_path),
         "--detach-sign", str(data_path)])


def private_path(path, *, directory=False):
    metadata = path.lstat()
    require((stat.S_ISDIR if directory else stat.S_ISREG)(metadata.st_mode)
            and metadata.st_uid == os.getuid() and metadata.st_mode & 0o077 == 0,
            "signing inputs must be owner-only regular files/directories, not symlinks")
    return path


def load_identity(config_path):
    private_path(config_path.parent, directory=True)
    config = json.loads(private_path(config_path).read_text(encoding="utf-8"))
    require(isinstance(config, dict), "invalid local signing configuration")
    require(re.fullmatch(r"[0-9a-f]{64}", config.get("certificateSha256", "")), "pin the signing certificate SHA-256")
    require(re.fullmatch(r"[A-Z0-9]{10}", config.get("teamId", "")), "invalid signing team ID")
    require(re.fullmatch(r"[A-Z0-9]{10}", config.get("notaryKeyId", "")), "invalid notarization key ID")
    require(re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", config.get("notaryIssuerId", "")), "invalid notarization issuer ID")
    directory = private_path(config_path.parent / config["credentialsDirectory"], directory=True)
    p12 = private_path(directory / "developer-id.p12")
    password = private_path(directory / "developer-id.p12.password")
    notary_key = private_path(directory / f"AuthKey_{config['notaryKeyId']}.p8")
    certificate = run(["openssl", "pkcs12", "-in", str(p12), "-passin", f"file:{password}", "-clcerts", "-nokeys"])
    der = run(["openssl", "x509", "-outform", "DER"], data=certificate)
    require(hashlib.sha256(der).hexdigest() == config["certificateSha256"], "signing certificate does not match the configured pin")
    run(["openssl", "x509", "-checkend", "0", "-noout"], data=certificate)
    subject = run(["openssl", "x509", "-noout", "-subject", "-nameopt", "sep_multiline,sname"], data=certificate).decode("utf-8")
    identity = re.search(r"^\s*CN=(Developer ID Application: [^\r\n]+)$", subject, re.M)
    team = re.search(r"^\s*OU=([A-Z0-9]{10})$", subject, re.M)
    require(identity and team and team[1] == config["teamId"], "expected a Developer ID Application certificate for the pinned team")
    bundle = run(["openssl", "pkcs12", "-in", str(p12), "-passin", f"file:{password}", "-nodes"])
    key_public = run(["openssl", "pkey", "-pubout", "-outform", "DER"], data=bundle)
    cert_public = run(["openssl", "x509", "-pubkey", "-noout"], data=certificate)
    cert_public = run(["openssl", "pkey", "-pubin", "-outform", "DER"], data=cert_public)
    require(key_public == cert_public, "saved private key does not match the signing certificate")
    return config, identity[1], hashlib.sha1(der).hexdigest().upper(), bundle, notary_key


@contextmanager
def restored(config_path=None):
    require(sys.platform == "darwin", "automatic Developer ID signing requires macOS")
    config_path = Path(config_path) if config_path else Path.home() / "Library/Application Support/Mont/Signing/release.json"
    require(config_path.is_file(), "local signing is not configured: create ~/Library/Application Support/Mont/Signing/release.json")
    config, identity, fingerprint, bundle, notary_key = load_identity(config_path)
    password = secrets.token_urlsafe(48)
    with tempfile.TemporaryDirectory(prefix="mont-release-signing-") as temporary:
        root = Path(temporary)
        certificate = root / "certificate.pem"
        keychain = root / "signing.keychain-db"
        converted = root / "import.p12"
        try:
            run(["security", "create-keychain", "-p", password, str(keychain)])
            run(["security", "set-keychain-settings", "-lut", "21600", str(keychain)])
            run(["security", "unlock-keychain", "-p", password, str(keychain)])
            # Keep the private key in memory; OpenSSL reads it once from stdin.
            certificate.write_bytes(run(["openssl", "x509"], data=bundle))
            converted.write_bytes(run(["openssl", "pkcs12", "-export", "-in", str(certificate), "-inkey", "/dev/stdin", "-keypbe", "PBE-SHA1-3DES", "-certpbe", "PBE-SHA1-3DES", "-macalg", "sha1", "-passout", "env:MONT_SIGNING_IMPORT_PASSWORD"], data=bundle, env={**os.environ, "MONT_SIGNING_IMPORT_PASSWORD": password}))
            converted.chmod(0o600)
            del bundle
            run(["security", "import", str(converted), "-P", password, "-T", "/usr/bin/codesign", "-t", "cert", "-f", "pkcs12", "-k", str(keychain)])
            converted.unlink()
            run(["security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-k", password, str(keychain)])
            identities = run(["security", "find-identity", "-v", "-p", "codesigning", str(keychain)]).decode("utf-8")
            require(re.search(rf'\b{fingerprint}\s+"{re.escape(identity)}"', identities), "pinned Developer ID is not a valid signing identity")
            # codesign also requires registration; retain every existing keychain.
            search = shlex.split(run(["security", "list-keychains", "-d", "user"]).decode("utf-8"))
            run(["security", "list-keychains", "-d", "user", "-s", *search, str(keychain)])
            run(["xcrun", "notarytool", "store-credentials", "mont-cli", "--key", str(notary_key), "--key-id", config["notaryKeyId"], "--issuer", config["notaryIssuerId"], "--keychain", str(keychain)])
            yield {"identity": identity, "identity_sha1": fingerprint, "keychain": str(keychain), "notary_profile": "mont-cli"}
        finally:
            if keychain.exists():
                # Deletion unregisters only this keychain, not a stale saved list.
                run(["security", "delete-keychain", str(keychain)])


@contextmanager
def credentials(args, target):
    supplied = any(getattr(args, name, None) for name in ("identity", "keychain", "notary_profile"))
    if args.unsigned or target != "aarch64-apple-darwin" or supplied:
        yield args
        return
    with restored() as selected:
        prepared = copy.copy(args)
        for name, value in selected.items():
            setattr(prepared, name, value)
        yield prepared


def main():
    try:
        with restored() as selected:
            # Exercise the actual private key without touching the installed CLI.
            probe = Path(selected["keychain"]).parent / "probe"
            shutil.copyfile("/usr/bin/true", probe)
            probe.chmod(0o700)
            run(["codesign", "--force", "--sign", selected["identity_sha1"], "--identifier", "mont-signing-preflight", "--options", "runtime", "--timestamp", "--keychain", selected["keychain"], str(probe)])
            run(["codesign", "--verify", "--strict", str(probe)])
            prefix = str(probe.parent / "certificate")
            run(["codesign", "-d", "--extract-certificates=" + prefix, str(probe)])
            require(hashlib.sha1(Path(prefix + "0").read_bytes()).hexdigest().upper() == selected["identity_sha1"], "preflight used an unexpected certificate")
        print("Signing ready: pinned Developer ID restored, signing verified, Apple notarization credentials validated; temporary keychain removed.")
    except (SigningError, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, SigningError) else type(error).__name__
        print(f"signing: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
