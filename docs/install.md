# Install Mont CLI

Mont CLI creates projects from templates, edits them locally in your browser, and exports MP4s from the browser or CLI. No Rust toolchain, SDK, or source checkout is needed. The public npm entry point is `usemont`, currently **0.1.0-preview.17** on macOS; archive and Homebrew channels are independent.

Mont CLI is proprietary software from Nauchu AB. The release's `TERMS.txt` governs use; third-party licenses and notices accompany the binary.

FFmpeg and ffprobe are separate dependencies for video/audio import and MP4 rendering. Homebrew and APT install them; npm/npx and manual archive users must install them separately. Intel macOS and Alpine/musl are not supported preview targets.

Releases that bundle `editor/` can launch the existing Flutter web editor with
`mont edit`, without installing Flutter or checking out source. Keep `editor/`
beside `bin/` when using an extracted archive. Homebrew installs it under
`share/mont/editor`; Debian packages use `/usr/share/mont/editor`. Older archives
without editor assets remain verifiable and installable, but do not provide the
bundled editor. `MONT_EDITOR_DIR` can point to a development web build.

## macOS

Apple Silicon (ARM64), macOS 14 or newer.

Download the signed, Apple-notarized package from the
[preview.15 release](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.15)
under Nauchu AB's proprietary preview terms. For manual installation:

1. Download its `darwin-arm64.tar.gz`, `SHA256SUMS`, and `release.json` from the
   same release. Do not substitute an `unsigned` archive.
2. In a new download directory, run `shasum -a 256 -c SHA256SUMS`. Every listed
   file must report `OK`. A checksum detects corruption; trusted release origin,
   signing, and notarization are separate requirements.
3. Extract the archive with `tar -xzf` followed by its downloaded filename, then
   enter its single `mont-VERSION-darwin-arm64` directory.
4. Verify Apple trust before running:

   ```sh
   codesign --verify --strict --verbose=2 bin/mont
   codesign --verify --strict -R=notarized --check-notarization --verbose=2 bin/mont
   ./bin/mont --version
   ./bin/mont --help
   ```

   Stop if either trust check fails. Keep the complete package, including
   `TERMS.txt`, `THIRD_PARTY_NOTICES.txt`, and `SBOM.json`. Run `./bin/mont`
   directly, or install it in a directory on your `PATH` according to your
   normal local policy.

   Standalone executables cannot carry a stapled notarization ticket. Apple trust
   verification and first launch may need internet access to retrieve that ticket;
   the local workspace workflow is offline after installation.

Alternatively, install from the official Homebrew tap, whose release channel is independent of npm:

```sh
brew install usemont/tap/usemont
# If an earlier Mont preview is already installed:
brew update
brew upgrade usemont/tap/usemont
```

The tap installs the same signed ARM64 binary package, not private Mont source,
and declares FFmpeg as a separate Homebrew dependency.

Never remove quarantine attributes, disable Gatekeeper, or bypass a security warning.

### npm / npx

`usemont@0.1.0-preview.17` supports Apple Silicon, macOS 14+, and Node.js 22+.
Fresh native registry acceptance passed with Node 22.14.0/npm 10.9.2 and
Node 24.7.0/npm 11.5.1, including codec-free `morph-text` preview.
Windows, Linux and Intel macOS npm payloads are not published.
No Homebrew CLI installation is required.

```sh
npx usemont templates preview morph-text
# Exact version for reproducible CI:
npx --yes usemont@0.1.0-preview.17 templates preview morph-text
```

Keep optional dependencies enabled: npm selects the exact native payload.
No lifecycle scripts or install-time binary download hooks are needed. Version,
help, project creation, the editor and companion commands work without FFmpeg;
media import/rendering require separately installed FFmpeg/ffprobe and a supported
native GPU backend. For example, install the codecs with `brew install ffmpeg`.

For a global installation without replacing Homebrew's executable:

```sh
npm install --global --prefix "$HOME/.local/mont-npm" --ignore-scripts usemont@0.1.0-preview.17
"$HOME/.local/mont-npm/bin/mont" --version
```

For an exact project dependency, commit both `package.json` and its lockfile:

```sh
npm install --save-dev --save-exact --ignore-scripts usemont@0.1.0-preview.17
npm exec --offline -- mont --version
```

The default (`latest`) channel currently selects `0.1.0-preview.17`, as
explicitly authorized for this initial release. It is a preview, not an accepted
stable release. The separate `preview` tag still awaits npm authorization;
use the verified bare or exact-version commands above meanwhile.
Exact project dependencies and lockfiles remain pinned.

### Migrating an existing scoped npm install

`@usemont/cli` is no longer the active root; already published versions remain
immutable. Native payload names such as `@usemont/cli-darwin-arm64` do not change,
and the executable remains `mont`. No installer automatically removes or replaces
your existing scoped install.

For a project, explicitly run `npm uninstall --save-dev @usemont/cli`, then the
exact `npm install --save-dev` command above, and commit `package.json` and its
lockfile. Existing projects stay pinned until explicitly migrated. For a global
install, use the same original `--prefix` when explicitly uninstalling
`@usemont/cli`, then install `usemont` in that prefix. Do not remove another
installer's `mont` link or use `--force`.

## Linux

Linux remains on preview.8; local template creation and browser editing require macOS preview.10.

### Signed APT preview channel

Debian 12 ARM64 and amd64 are the accepted baseline. A Vulkan driver is required;
the package includes a Mesa software-driver dependency for CPU-only machines.
Emulated x64 and software rendering establish correctness, not performance.

Run once to register the official repository. The public key download is checked
against a pinned SHA-256 digest **before** trusting it:

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl
(
  set -eu
  key="$(mktemp)"
  trap 'rm -f "$key"' EXIT
  curl -fsSL https://usemont.github.io/mont-cli/apt/mont-archive-keyring.gpg -o "$key"
  printf '%s  %s\n' '7e9adab2f69ebb6c2d293d56ed92a00546e86c1b547274188a2c365b6e561729' "$key" |
    sha256sum --check --status
  sudo install -m 0644 "$key" /usr/share/keyrings/mont-archive-keyring.gpg
)
```

Stop if download or checksum verification fails. The key's primary fingerprint
is `0B68 A80F 5ACD 90F6 31F8 BEA9 61E6 F5B0 61DE 5189`.
Then install:

```sh
printf '%s\n' 'deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/mont-archive-keyring.gpg] https://usemont.github.io/mont-cli/apt preview main' |
  sudo tee /etc/apt/sources.list.d/mont.list
sudo apt-get update
sudo apt-get install mont
mont --version
```

Updates use the same signed channel; no reinstall or source checkout:

```sh
sudo apt-get update
sudo apt-get install --only-upgrade mont
```

APT verifies the signed repository metadata and package hashes. Do not use
`apt-key`, `trusted=yes`, or `--allow-unauthenticated`. The `.deb` installs
`/usr/bin/mont` and legal notices, SBOM, provenance, and editable-project examples
under `/usr/share/mont/`; it does not modify user workspaces. This application-data
location preserves examples and terms on minimal Debian images that strip
`/usr/share/doc`. Uninstall with
`sudo apt-get remove mont`. Existing internal `mont-cli-internal` packages must
be removed first; they are not the public update channel.

The [release assets](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.8)
also provide Linux archives. Each architecture has its own `release.json`,
`SHA256SUMS`, and detached OpenPGP signature, published with architecture-prefixed
filenames to avoid collisions. APT is the recommended installation path.

### Manual archive trust verification

Use the architecture-matched Linux archive and its matching architecture-prefixed `release.json`, `SHA256SUMS`, and `SHA256SUMS.asc` from the same [preview.8 release](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.8). Do not substitute an unsigned archive.

Before extracting or running anything, obtain the public keyring independently using the pinned download and digest check above. Verify its primary fingerprint against `0B68 A80F 5ACD 90F6 31F8 BEA9 61E6 F5B0 61DE 5189`; do not trust a key embedded in the archive. Use GnuPG to verify the detached OpenPGP signature over the matching checksum file with that keyring, then verify the archive and manifest hashes against that authenticated checksum file. Stop on any signature, fingerprint, or checksum failure. A checksum alone does not establish trusted origin.

Extract into a new directory only after verification. Keep the complete package, including its terms, third-party notices, and SBOM; run `./bin/mont --version` from the extracted directory. Manual archives do not configure an update channel: repeat these checks for every new release, or use signed APT instead.

## Update ownership and multiple installations

Use the installer that owns the installation: Homebrew uses `brew update && brew upgrade usemont/tap/usemont`; APT uses the signed-channel update commands above. Manual archives require downloading and verifying a new release. Mont does not provide a self-update command. The Homebrew formula name is `usemont`, while its executable remains `mont`.

**npm/npx supports Apple Silicon macOS 14+ only**, with Node.js 22+.
Windows x64 and Linux x64/ARM64 with glibc 2.36+ (Debian 12 baseline) remain
planned npm targets, not published availability. Intel macOS and Alpine/musl
are not supported. Native Windows and Linux npm acceptance remains a release gate.
Install optional dependencies: they contain the matching native payload.
Installation uses no lifecycle scripts or binary downloads outside npm.

Version/help, project creation, and the editor do not require FFmpeg. Media
commands currently require external `ffmpeg` and `ffprobe`; npm neither bundles
nor installs codecs, GPU drivers, or hardware-acceleration support.
Future Linux npm payloads also need the OpenSSL 3 and C++ runtimes. Rendering
requires a Vulkan loader and driver; Mesa's software driver supports CPU-only
machines. Unlike APT, npm does not resolve these OS dependencies.

| Installation | Version selection / update |
| --- | --- |
| One-off npx | `npx usemont templates preview morph-text` uses the default channel; use an exact version for reproducibility. |
| Global npm | `npm install -g --prefix "$HOME/.local/mont-npm" usemont@0.1.0-preview.17` selects that version for the global installation. |
| Project dependency | Update the explicit dependency and commit its lockfile; use `npm ci` in CI. |

Normal prerelease promotion uses `preview`; stable promotion uses `latest`. This initial `usemont` publication has an owner-authorized default-channel exception: `latest` selects preview.17 without claiming stable acceptance. The separate `preview` tag is not yet available. Bare `npx usemont` can use a project-local dependency; it is not a guarantee of the newest release. Do not clear npm's cache as an update procedure. npm does not install Mont's external FFmpeg/ffprobe dependency.

Installations remain independent. Plain `mont` follows shell command resolution; inspect `type -a mont` on POSIX or `Get-Command mont -All` in PowerShell, then run `mont --version`. The npm launcher selects its own exact-version platform payload, never another `mont` on `PATH`; a missing payload is an error, not a fallback.
If global npm and Homebrew share a `bin` directory, do not use npm `--force` to
replace Homebrew's `mont` link. Use npx or a separate npm prefix instead.

Updating one installation must not uninstall or overwrite another installer's package. The existing optional `mont mcp install` integration is separate: it refreshes the shared, stable user-owned MCP launcher; manual archive and APT users run it explicitly after an upgrade. Homebrew's post-install step provisions it automatically. An npm upgrade alone does not refresh it.

An npm/npx rollback selects a previously published exact version. Homebrew rollback requires an available versioned formula or a verified older archive; there is no promised generic downgrade command. Moving a channel tag does not update pinned versions or running processes, and binary rollback does not guarantee an older binary can open a newer project format.
