#!/usr/bin/env python3
"""Fail-closed acceptance/protection gates and an allowlisted public publisher export."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import npm_publish


CLIENTS = {("v22.14.0", "10.9.2"), ("v24.7.0", "11.5.1")}
TARGETS = {"darwin-arm64", "win32-x64", "linux-x64", "linux-arm64"}
CHECKS = {"render", "assets", "coreWithoutCodecs", "cancellation"}
require = npm_publish.require


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_files(directory):
    paths = sorted(Path(directory).iterdir())
    require(all(path.is_file() and not path.is_symlink() and path.suffix == ".json" for path in paths),
            "Evidence must contain only regular JSON report basenames")
    return paths


def evidence_digest(reports):
    hashes = {path.name: sha256(path) for path in report_files(reports)}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(output, reports, mode, expected_index_sha256, expected_evidence_sha256):
    output = Path(output)
    require(mode in {"packed", "registry"}, "Unknown acceptance mode")
    require(sha256(output / "index.json") == expected_index_sha256, "Approved index digest differs")
    index = npm_publish.pack(output)
    require(evidence_digest(reports) == expected_evidence_sha256, "Approved evidence digest differs")
    paths = report_files(reports)
    require(len(paths) == 8, "Exactly eight native acceptance reports are required")
    expected_packages = {item["name"]: item["integrity"] for item in index["packages"]}
    seen = set()
    for path in paths:
        report = npm_publish.read_json(path)
        target = report.get("target")
        client = (report.get("node"), report.get("npm"))
        require(target in TARGETS and client in CLIENTS, "Unapproved target/client pair")
        key = (target, client)
        require(key not in seen, "Duplicate target/client evidence")
        seen.add(key)
        require(report.get("schemaVersion") == 1 and report.get("mode") == mode
                and all(report.get(field) == index[field]
                        for field in ("version", "sourceCommit", "launcherVersion"))
                and report.get("indexSha256") == expected_index_sha256
                and report.get("packages") == expected_packages, "Acceptance identity differs from approved packages")
        machine = report.get("hostMachine", "").lower()
        native = {"arm64", "aarch64"} if target.endswith("arm64") else {"amd64", "x86_64"}
        require(machine in native, "Acceptance did not run on the native host architecture")
        require(report.get("checks") == dict.fromkeys(CHECKS, True), "Acceptance checks are incomplete")
        if target.startswith("linux-"):
            require(report.get("libc") == "glibc 2.36", "Linux acceptance requires the Debian 12 glibc baseline")
        if mode == "packed":
            require(report.get("nativeTrust") == {"distribution": "public",
                    "nativeArchiveVerification": "release.verify portable=False"},
                    "Native signed archive verification is missing")
        if target == "win32-x64":
            signatures = report.get("authenticode", [])
            require(isinstance(signatures, list) and len(signatures) == 2
                    and {item.get("file") for item in signatures} == {"mont.exe", "mont-mcp.exe"}
                    and all("Nauchu AB" in item.get("subject", "") and item.get("timestampSubject")
                            for item in signatures), "Windows publisher/timestamp acceptance is missing")
    require(seen == {(target, client) for target in TARGETS for client in CLIENTS}, "Incomplete native matrix")
    return {"version": index["version"], "mode": mode, "indexSha256": expected_index_sha256,
            "evidenceSha256": expected_evidence_sha256, "reports": len(paths)}


def validate_protection(repository, protection, environment, policies, branch):
    require(repository.get("full_name") == "usemont/mont-cli" and repository.get("visibility") == "public"
            and repository.get("default_branch") == branch == "main", "Publisher requires public usemont/mont-cli main")
    require(protection.get("enforce_admins", {}).get("enabled") is True
            and protection.get("allow_force_pushes", {}).get("enabled") is False
            and protection.get("allow_deletions", {}).get("enabled") is False,
            "Main branch must enforce protection for admins and prohibit force pushes/deletion")
    rules = environment.get("protection_rules", [])
    require(environment.get("can_admins_bypass") is False
            and any(rule.get("type") == "required_reviewers" and rule.get("reviewers") for rule in rules),
            "Publishing environment requires reviewers with admin bypass disabled")
    require(environment.get("deployment_branch_policy") == {"protected_branches": False, "custom_branch_policies": True}
            and policies.get("total_count") == 1 and len(policies.get("branch_policies", [])) == 1
            and policies["branch_policies"][0].get("name") == branch
            and policies["branch_policies"][0].get("type") == "branch", "Publishing environment must allow only main branches")


def gh(path):
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def protection(repository, branch, environment, workflow):
    require(repository == "usemont/mont-cli" and branch == "main" and environment == "mont-cli-public"
            and workflow == "npm-release.yml", "Unexpected publisher identity")
    base = "repos/" + repository
    validate_protection(gh(base), gh(base + "/branches/main/protection"),
                        gh(base + "/environments/" + environment),
                        gh(base + "/environments/" + environment + "/deployment-branch-policies"), branch)
    data = gh(base + "/actions/workflows/" + workflow)
    require(data.get("path") == ".github/workflows/npm-release.yml" and data.get("state") == "active",
            "Reviewed publisher workflow is not active")
    return {"repository": repository, "branch": branch, "environment": environment, "workflow": workflow}


def export(output):
    """Copy only reviewed tooling/public docs; never native source, fixtures or credentials."""
    output = Path(output).absolute()
    require(not output.exists(), "Export output must not already exist")
    tool = Path(__file__).resolve().parent
    public = tool.parent / "public"
    paths = {"tool/" + name: tool / name for name in (
        "npm_release_gate.py", "npm_publish.py", "npm_package.py", "npm_acceptance.py",
        "npm-launcher.cjs", "release.py", "signing.py")}
    paths.update({name: public / name for name in ("README.md", "docs/install.md", ".github/workflows/npm-release.yml")})
    require(all(path.is_file() and not path.is_symlink() for path in paths.values()), "Publisher export input is missing/unsafe")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mont-public-export-", dir=output.parent) as temporary:
        stage = Path(temporary) / "publisher"
        for name, source in paths.items():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        stage.rename(output)
    return {name: sha256(output / name) for name in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--output", required=True, type=Path)
    check.add_argument("--reports", required=True, type=Path)
    check.add_argument("--mode", required=True, choices=("packed", "registry"))
    check.add_argument("--expected-index-sha256", required=True)
    check.add_argument("--expected-evidence-sha256", required=True)
    protected = commands.add_parser("protection")
    protected.add_argument("--repository", required=True)
    protected.add_argument("--branch", required=True)
    protected.add_argument("--environment", required=True)
    protected.add_argument("--workflow", required=True)
    exported = commands.add_parser("export")
    exported.add_argument("--output", required=True, type=Path)
    digest = commands.add_parser("evidence-digest")
    digest.add_argument("--reports", required=True, type=Path)
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        result = {"verify": verify, "protection": protection, "export": export,
                  "evidence-digest": evidence_digest}[command](**args)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"npm-release-gate: {error}\n")


if __name__ == "__main__":
    main()
