#!/usr/bin/env python3
"""Exercise real npm-installed Mont and bundled assets, never source-tree assets."""
import argparse
import base64
import hashlib
import json
import os
import platform
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request


PACKAGE = "@usemont/cli"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, cwd, env, timeout=900):
    result = subprocess.run([str(item) for item in command], cwd=cwd, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    require(result.returncode == 0,
            f"Command failed ({result.returncode}): {command!r}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def integrity(path):
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


def isolated_environment(home, tools, node):
    # No inherited auth, runtime overrides, NODE_OPTIONS, or checkout paths.
    environment = {key: value for key, value in os.environ.items()
                   if os.name == "nt" and key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
    if os.name == "nt":
        system_root = environment.get("SystemRoot") or environment.get("SYSTEMROOT") or os.environ.get("WINDIR")
        require(system_root, "Windows acceptance requires SystemRoot")
        system_path = [str(Path(system_root) / "System32"), system_root,
                       str(Path(system_root) / "System32/WindowsPowerShell/v1.0")]
    else:
        system_path = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    runtime = home / "runtime"
    runtime.mkdir(mode=0o700)
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
                       XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"),
                       XDG_CACHE_HOME=str(home / "cache"), XDG_RUNTIME_DIR=str(runtime),
                       TMPDIR=str(home), TEMP=str(home), TMP=str(home), LANG="en_US.UTF-8",
                       PATH=os.pathsep.join(dict.fromkeys([str(node.parent),
                           *(str(path.parent) for path in tools.values()), *system_path])),
                       RENDER_WORKER_FFMPEG_PATH=str(tools["ffmpeg"]),
                       RENDER_WORKER_FFPROBE_PATH=str(tools["ffprobe"]),
                       FFMPEG_PATH=str(tools["ffmpeg"]), FFPROBE_PATH=str(tools["ffprobe"]),
                       npm_config_userconfig=str(home / "npmrc"), npm_config_globalconfig=str(home / "global-npmrc"),
                       npm_config_cache=str(home / "npm-cache"), npm_config_ignore_scripts="true",
                       npm_config_audit="false", npm_config_fund="false", npm_config_update_notifier="false")
    return environment


def core_environment(environment, node):
    """Prove core commands work with no external codec binaries or overrides."""
    core = {key: value for key, value in environment.items()
            if key not in {"FFMPEG_PATH", "FFPROBE_PATH"} and not key.startswith("RENDER_WORKER_")}
    paths = [str(node.parent)]
    if os.name == "nt":
        root = core.get("SystemRoot") or core.get("SYSTEMROOT") or core["WINDIR"]
        paths.extend([str(Path(root) / "System32"), root,
                      str(Path(root) / "System32/WindowsPowerShell/v1.0")])
    core["PATH"] = os.pathsep.join(paths)
    require(all(shutil.which(tool, path=core["PATH"]) is None for tool in ("ffmpeg", "ffprobe")),
            "Core acceptance PATH must not expose FFmpeg/ffprobe")
    return core


def windows_signatures(payload, expected_publisher, cwd, env):
    require(expected_publisher and "Nauchu AB" in expected_publisher,
            "Windows public acceptance requires an independently approved Nauchu AB certificate subject")
    script = """
$ErrorActionPreference = 'Stop'
$results = @('mont.exe','mont-mcp.exe') | ForEach-Object {
  $s = Get-AuthenticodeSignature -LiteralPath (Join-Path $env:MONT_ACCEPTANCE_BIN $_)
  if ($s.Status -ne 'Valid' -or $null -eq $s.SignerCertificate -or
      $s.SignerCertificate.Subject -cne $env:MONT_ACCEPTANCE_PUBLISHER -or
      $null -eq $s.TimeStamperCertificate) { throw "Invalid publisher or timestamp: $_ ($($s.Status))" }
  @{file=$_;subject=$s.SignerCertificate.Subject;timestampSubject=$s.TimeStamperCertificate.Subject}
}
ConvertTo-Json -InputObject @($results) -Compress
"""
    return json.loads(run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], cwd,
                          {**env, "MONT_ACCEPTANCE_BIN": str(payload / "bin"),
                           "MONT_ACCEPTANCE_PUBLISHER": expected_publisher}))


def processes(cwd, env):
    if os.name == "nt":
        rows = json.loads(run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                              "ConvertTo-Json -InputObject @(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId) -Compress"], cwd, env))
        return {int(row["ProcessId"]): int(row["ParentProcessId"]) for row in rows}
    result = run(["/bin/ps", "-axo", "pid=,ppid=,stat="], cwd, env)
    return {int(parts[0]): int(parts[1]) for line in result.splitlines()
            if len(parts := line.split()) == 3 and not parts[2].startswith("Z")}


def descendants(root, table):
    found = {root}
    while True:
        more = {pid for pid, parent in table.items() if parent in found}
        if more <= found:
            return found - {root}
        found |= more


def interrupt(process):
    if os.name != "nt":
        process.send_signal(signal.SIGINT)
        return "POSIX SIGINT sent only to Node launcher; native child exit observed"
    # A real Win32 console event, not Python's SIGINT/TerminateProcess emulation.
    helper = """
import ctypes, sys, time
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])): raise ctypes.WinError(ctypes.get_last_error())
if not k.SetConsoleCtrlHandler(None, True): raise ctypes.WinError(ctypes.get_last_error())
if not k.GenerateConsoleCtrlEvent(0, 0): raise ctypes.WinError(ctypes.get_last_error())
time.sleep(1)
k.FreeConsole()
"""
    subprocess.run([sys.executable, "-c", helper, str(process.pid)], check=True, timeout=15)
    return "Win32 AttachConsole/GenerateConsoleCtrlEvent CTRL_C_EVENT; physical interactive keyboard Ctrl-C not exercised"


def editor_acceptance(command, payload, workspace, cwd, env):
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
        process = subprocess.Popen([*command, "--json", "edit", "--web", "--no-open", str(workspace)],
                                   cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
                                   creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
        children = set()
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            try:
                ready = json.loads(lines.get(timeout=60))
            except (queue.Empty, json.JSONDecodeError) as error:
                errors.seek(0)
                raise ValueError(f"Bundled editor failed readiness: {errors.read()}") from error
            require(ready.get("ok") is True, f"Editor failed: {ready}")
            connection = json.loads(Path(ready["result"]["connectionFile"]).read_text(encoding="utf-8"))
            children = descendants(process.pid, processes(cwd, env))
            require(children, "Node launcher did not retain a real native editor child")
            url = connection["url"].rstrip("/")
            for relative in ("index.html", "flutter_bootstrap.js", "main.dart.js"):
                with urllib.request.urlopen(url + "/" + relative, timeout=30) as response:
                    require(response.status == 200 and response.read() == (payload / "editor" / relative).read_bytes(),
                            f"HTTP editor asset differs from installed payload: {relative}")
            cancellation = interrupt(process)
            process.communicate(timeout=30)
            deadline = time.monotonic() + 10
            remaining = children
            while remaining and time.monotonic() < deadline:
                remaining = children & processes(cwd, env).keys()
                if remaining:
                    time.sleep(0.2)
            require(not remaining, f"Cancellation orphaned native processes: {sorted(remaining)}")
            try:
                urllib.request.urlopen(url + "/index.html", timeout=2).close()
            except OSError:
                pass
            else:
                raise ValueError("Cancelled editor still accepts HTTP connections")
            # A real mutating SDK operation proves the writer lock was released.
            envelope = cwd / "cancellation-reopen.json"
            envelope.write_text(json.dumps({"commandId": "npm-cancellation-reopen", "command": {
                "type": "setProjectValue", "pointer": "/title", "value": "Cancellation lock released"}}))
            run([*command, "-C", workspace, "dispatch", envelope], cwd, env)
            run([*command, "-C", workspace, "validate"], cwd, env)
            return cancellation
        finally:
            # Clean up failures too, including a native child whose launcher already exited.
            for pid in children & processes(cwd, env).keys():
                if os.name == "nt":
                    subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], env=env, capture_output=True)
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], env=env, capture_output=True)
                else:
                    process.kill()
                process.communicate(timeout=15)


def native_archive_acceptance(output, payload, archive):
    """Reuse native release trust/linkage checks before running installed Mont."""
    import release
    manifest = json.loads((payload / "release.json").read_text())
    public = manifest["distribution"] == "public"
    if archive is None and public:
        provenance = json.loads((output / "sources.json").read_text())
        candidates = []
        for value in provenance["archives"]:
            path = (output / value).resolve(strict=True)
            require(path.is_relative_to(output / "sources"), "Native source archive escapes preserved sources")
            if json.loads((path.parent / "release.json").read_text())["target"] == manifest["target"]:
                candidates.append(path)
        require(len(candidates) == 1, "Exactly one preserved native source archive is required")
        archive = candidates[0]
    if archive is None:
        return {"distribution": manifest["distribution"], "nativeArchiveVerification": "not requested for private local package"}
    archive = archive.resolve(strict=True)
    require(json.loads((archive.parent / "release.json").read_text()) == manifest,
            "Installed native manifest differs from preserved source")
    suffix = ".exe" if os.name == "nt" else ""
    for program in ("mont", "mont-mcp"):
        name = "bin/" + program + suffix
        require(hashlib.sha256((payload / name).read_bytes()).hexdigest() == manifest["files"][name],
                "Installed executable differs from native source manifest: " + name)
    # Windows installed signatures are checked first. Archive extraction verifies the
    # same hashes before verify() executes either native binary; no unsigned probe runs.
    release.verify(argparse.Namespace(archive=archive, extract=None, portable=False, require_signed=public))
    return {"distribution": manifest["distribution"], "nativeArchiveVerification": "release.verify portable=False"}


def exercise(command, payload, version, tools, cwd, env, core_env):
    manifest = json.loads((payload / "release.json").read_text())
    require(manifest["version"] == version, "Installed native manifest version differs")
    require(run([*command, "--version"], cwd, core_env) == "mont " + version, "Mont version mismatch")
    require("render" in run([*command, "--help"], cwd, core_env), "Mont help does not expose rendering")
    suffix = ".exe" if os.name == "nt" else ""
    require(run([payload / "bin" / ("mont-mcp" + suffix), "--version"], cwd, core_env)
            == "mont-mcp " + manifest["launcherVersion"], "Companion version mismatch")
    for recipe, destination in (("workflow.mont.json", "project-proof"),
                                ("video-workflow.mont.json", "video-proof")):
        run([*command, "-C", cwd / destination, "workflows", "run", payload / "example" / recipe,
             "--run-id", "npm-acceptance"], cwd, core_env if recipe == "workflow.mont.json" else env)
    project = cwd / "project-proof" / "project-bundle" / "project.json"
    require(json.loads(project.read_text())["title"] == "Edited from the portable bundle",
            "Shipped creation recipe did not retain its native edit")
    video_root = cwd / "video-proof"
    hashes = {}
    for name in ("source.mp4", "original.mp4", "revised.mp4", "revised-repeat.mp4", "bundle.mp4"):
        media = video_root / name
        metadata = json.loads(run([tools["ffprobe"], "-v", "error", "-count_frames", "-show_streams",
                                   "-show_format", "-of", "json", media], cwd, env))
        videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
        require(len(videos) == 1 and (videos[0]["width"], videos[0]["height"]) == (640, 360)
                and int(videos[0]["nb_read_frames"]) == 72
                and abs(float(metadata["format"]["duration"]) - 6) < 0.1,
                f"{name}: expected six seconds, 72 decoded 640x360 frames")
        run([tools["ffmpeg"], "-v", "error", "-xerror", "-i", media, "-f", "null", "-"], cwd, env)
        hashes[name] = run([tools["ffmpeg"], "-v", "error", "-i", media, "-map", "0:v:0",
                            "-f", "hash", "-hash", "sha256", "pipe:1"], cwd, env)
    require(hashes["original.mp4"] != hashes["revised.mp4"], "Native edit did not change rendered pixels")
    require(hashes["revised.mp4"] == hashes["revised-repeat.mp4"] == hashes["bundle.mp4"],
            "Repeated/portable native renders differ")
    cancellation = editor_acceptance(command, payload, video_root / "video-bundle", cwd, core_env)
    return {"version": version, "launcherVersion": manifest["launcherVersion"],
            "render": "real packaged video workflow: decoded output and revised pixels verified",
            "assets": "packaged creation workflow and byte-identical bundled editor HTTP assets",
            "coreWithoutCodecs": "version/help, companion, creation recipe, editor HTTP and cancellation",
            "cancellation": cancellation,
            "checks": dict.fromkeys(("render", "assets", "coreWithoutCodecs", "cancellation"), True)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Durable npm stage/candidate directory")
    parser.add_argument("--mode", choices=("packed", "registry"), required=True)
    parser.add_argument("--ffmpeg", required=True, type=Path)
    parser.add_argument("--ffprobe", required=True, type=Path)
    parser.add_argument("--expected-windows-publisher", default=os.environ.get("MONT_CLI_WINDOWS_PUBLISHER"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--native-archive", type=Path, help="Optional source archive for private local native verification")
    args = parser.parse_args()
    try:
        output = args.output.resolve(strict=True)
        index = json.loads((output / "index.json").read_text())
        version = index["version"]
        node_path, npm_path = shutil.which("node"), shutil.which("npm")
        require(node_path and npm_path, "Node.js and npm must be independently installed")
        node = Path(node_path).resolve(strict=True)
        # npm.cmd cannot be passed to CreateProcess without cmd.exe; invoke npm's real JS entry.
        npm_cli = Path(npm_path).parent / "node_modules/npm/bin/npm-cli.js" if os.name == "nt" else Path(npm_path).resolve()
        require(npm_cli.is_file(), "Cannot locate installed npm-cli.js")
        npm = [str(node), str(npm_cli)]
        host = json.loads(run([node, "-p", "JSON.stringify({platform:process.platform,arch:process.arch})"], output, os.environ))
        target = host["platform"] + "-" + host["arch"]
        require(target in {"darwin-arm64", "win32-x64", "linux-x64", "linux-arm64"}, "Unsupported native acceptance host")
        tools = {name: getattr(args, name).resolve(strict=True) for name in ("ffmpeg", "ffprobe")}
        require(all(path.is_file() and os.access(path, os.X_OK) for path in tools.values()), "External FFmpeg/ffprobe must be executable")
        records = {item["name"]: item for item in index["packages"]}
        payload_name = PACKAGE + "-" + target
        require(PACKAGE in records and payload_name in records, "Package index lacks native root/payload")
        receipt = json.loads((output / "receipt.json").read_text()) if args.mode == "registry" else None
        if receipt:
            require(receipt["version"] == version, "Candidate receipt version differs")
            require({item["name"]: item["integrity"] for item in receipt["packages"]}
                    == {name: item["integrity"] for name, item in records.items()}, "Candidate receipt integrity differs")
        with tempfile.TemporaryDirectory(prefix="mont npm native ü ") as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            cwd = Path(temporary) / "working directory 雪"
            cwd.mkdir()
            env = isolated_environment(home, tools, node)
            env["npm_config_registry"] = receipt["registry"] if receipt else "https://registry.npmjs.org/"
            core_env = core_environment(env, node)
            registry_command = None
            if args.mode == "packed":
                tarballs = []
                for name in (payload_name, PACKAGE):
                    path = (output / records[name]["tarball"]).resolve(strict=True)
                    require(path.is_relative_to(output) and integrity(path) == records[name]["integrity"], "Packed tarball integrity differs")
                    tarballs.append(path)
                (cwd / "package.json").write_text('{"private":true}')
                run([*npm, "install", "--ignore-scripts", "--offline", "--no-audit", "--no-fund", *tarballs], cwd, env)
                command = [str(node), str(cwd / "node_modules/@usemont/cli/mont.cjs")]
                payload = cwd / "node_modules/@usemont" / ("cli-" + target)
            else:
                command = [*npm, "exec", "--yes", "--ignore-scripts", "--registry", receipt["registry"],
                           "--package=" + PACKAGE + "@" + version, "--", "mont"]
                registry_command = command
                # Provision npm's actual exact-version exec cache without running native
                # code until the Windows Authenticode and installed integrity gates pass.
                run([*command[:-1], "node", "-e", "process.exit(0)"], cwd, env)
                installed = list((home / "npm-cache/_npx").glob("*/node_modules/@usemont/cli/package.json"))
                require(len(installed) == 1, "Expected one fresh exact registry npm exec installation")
                payload = installed[0].parent.parent / ("cli-" + target)
                installed_root = installed[0].parent
                lock = json.loads((installed_root.parents[2] / "package-lock.json").read_text())
                for name in (PACKAGE, payload_name):
                    metadata = lock["packages"]["node_modules/" + name]
                    require(metadata["version"] == version and metadata["integrity"] == records[name]["integrity"],
                            f"Registry installation integrity differs from accepted tarball: {name}")
                # npm exec above is the actual registry entry point. Signal the installed
                # Node launcher directly, so this check measures Mont, not npm's shell.
                command = [str(node), str(installed_root / "mont.cjs")]
            require(json.loads((payload / "package.json").read_text())["version"] == version, "Installed payload version mismatch")
            signatures = windows_signatures(payload, args.expected_windows_publisher, cwd, env) if os.name == "nt" else None
            native_trust = native_archive_acceptance(output, payload, args.native_archive) if args.mode == "packed" else None
            if registry_command:
                require(json.loads((payload / "release.json").read_text())["distribution"] == "public",
                        "Registry native acceptance requires a public payload")
                require(run([*registry_command, "--version"], cwd, core_env) == "mont " + version, "Exact registry npm exec failed")
                require("render" in run([*registry_command, "--help"], cwd, core_env), "Exact registry npm exec help failed")
            report = {"schemaVersion": 1, "mode": args.mode, "target": target,
                      "hostMachine": platform.machine(), "sourceCommit": index["sourceCommit"],
                      "indexSha256": hashlib.sha256((output / "index.json").read_bytes()).hexdigest(),
                      "packages": {name: item["integrity"] for name, item in records.items()},
                      **exercise(command, payload, version, tools, cwd, env, core_env)}
            report["node"] = run([node, "--version"], cwd, env)
            report["npm"] = run([*npm, "--version"], cwd, env)
            if target.startswith("linux-"):
                report["libc"] = " ".join(platform.libc_ver())
            if signatures:
                report["authenticode"] = signatures
            if native_trust:
                report["nativeTrust"] = native_trust
            if args.report:
                args.report.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"npm-acceptance: {error}\n")


if __name__ == "__main__":
    main()
