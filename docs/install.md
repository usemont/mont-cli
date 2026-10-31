# Install Mont CLI

Install **0.1.0-preview.6** for `mont <template-name>` previews. No Rust toolchain, SDK, or source checkout is needed.

Mont CLI is proprietary software from Nauchu AB. The release's `TERMS.txt` governs use; third-party licenses and notices accompany the binary.

FFmpeg and ffprobe are separate dependencies for video/audio import and MP4 rendering. Homebrew and APT install them; manual archive users must install them separately. Intel macOS and Alpine/musl are not supported preview targets.

## macOS

Apple Silicon (ARM64), macOS 14 or newer.

Signed, Apple-notarized assets are published in the
[preview.6 release](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.6)
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

Alternatively, install the same release from the official Homebrew tap:

```sh
brew install usemont/tap/mont
# If an earlier Mont preview is already installed:
brew update
brew upgrade usemont/tap/mont
```

The tap installs the same signed ARM64 binary package, not private Mont source,
and declares FFmpeg as a separate Homebrew dependency.

Never remove quarantine attributes, disable Gatekeeper, or bypass a security warning.

## Linux

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

The [release assets](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.6)
also provide Linux archives. Each architecture has its own `release.json`,
`SHA256SUMS`, and detached OpenPGP signature, published with architecture-prefixed
filenames to avoid collisions. APT is the recommended installation path.

### Manual archive trust verification

Use the architecture-matched Linux archive and its matching architecture-prefixed `release.json`, `SHA256SUMS`, and `SHA256SUMS.asc` from the same [preview.6 release](https://github.com/usemont/mont-cli/releases/tag/v0.1.0-preview.6). Do not substitute an unsigned archive.

Before extracting or running anything, obtain the public keyring independently using the pinned download and digest check above. Verify its primary fingerprint against `0B68 A80F 5ACD 90F6 31F8 BEA9 61E6 F5B0 61DE 5189`; do not trust a key embedded in the archive. Use GnuPG to verify the detached OpenPGP signature over the matching checksum file with that keyring, then verify the archive and manifest hashes against that authenticated checksum file. Stop on any signature, fingerprint, or checksum failure. A checksum alone does not establish trusted origin.

Extract into a new directory only after verification. Keep the complete package, including its terms, third-party notices, and SBOM; run `./bin/mont --version` from the extracted directory. Manual archives do not configure an update channel: repeat these checks for every new release, or use signed APT instead.
