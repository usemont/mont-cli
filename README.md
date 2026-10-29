# Mont CLI Developer Preview

`mont` is a native command-line surface for Mont SDK project workspaces:
native JSON edits, media ingestion, MP4 rendering, validation, history, explicit
synchronization, and relocatable offline bundles. Version **0.1.0-preview.2**
adds `import` and `render`; the earlier **0.1.0-preview.1 binary does not**.
Rendering requires separately installed FFmpeg/ffprobe and a usable native GPU.
Bundle export remains a project directory, not an MP4. Flutter/mobile cloud
roundtrips are not provided.

## Availability and rights

The developer preview is **proprietary, binary-only**, initially for **Apple
Silicon (ARM64), macOS 14 or newer**. The Mont monorepo and SDK remain private; downloading
the CLI does not grant access to either or a right to redistribute them.
Third-party component licenses and notices accompany the binary separately.
The approved `TERMS.txt` included in a public release governs use of Mont.

**Public distribution requires** Developer ID Application signing, successful
Apple notarization, and approved proprietary preview terms. These are release
gates, not optional installation steps. Unsigned archives are internal evaluation
artifacts only; do not publish them, add them to a public tap, or treat Apple
Development/Distribution signing as Developer ID signing. Never remove quarantine
attributes, disable Gatekeeper, or instruct users to bypass a security warning.
Internal ARM64 builds have an ad-hoc code seal for native execution only;
`unsigned` means no trusted Developer ID signature or Apple notarization.
An ad-hoc seal does not make an internal artifact suitable for public release.

## Install the released binary

No Rust toolchain, SDK, repository checkout, or build dependencies are needed.
Use an ARM64 Mac; Intel macOS, Linux, and Windows are not preview targets.
Python 3 is needed only for the bundled examples, not for `mont` itself.
For video/audio import and MP4 rendering, install FFmpeg and ffprobe separately
(`brew install ffmpeg`); their executables are not bundled with Mont.

Signed, Apple-notarized assets are published in the
[official releases](https://github.com/usemont/mont-cli/releases)
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

## Support and contributions

- [Report CLI bugs and feature requests](https://github.com/usemont/mont-cli/issues).
- [Report vulnerabilities privately](https://github.com/usemont/mont-cli/security/advisories/new);
  do not include credentials or sensitive project data in public issues.
- [Contribute original skills and recipes](https://github.com/usemont/skills/blob/main/CONTRIBUTING.md).
  Those contributions are Apache-2.0; the CLI, SDK, and engine remain proprietary.

## Standalone example

From the extracted package:

```sh
python3 example/workflow.py --mont ./bin/mont
# With mont installed on PATH:
python3 example/workflow.py
# An explicit binary can also be selected with MONT=/absolute/path/to/mont.
```

The standard-library-only script creates a temporary workspace, dispatches
native title and metadata edits, generates and registers an original 1×1 PNG,
validates, exports a relocatable bundle, deletes its own source workspace and
original, then reopens, checks, and edits the bundle. It asserts project
identity, metadata, original bytes, and history. All writes and deletions are
inside its own temporary directory, which is removed afterward. It requires
no account, network, Rust, repository, media decoder, or renderer.

For an editable video project, revision, portable bundle, and actual MP4 renders:

```sh
python3 example/video_workflow.py --mont ./bin/mont \
  --output /absolute/new/directory/video-proof
# Homebrew installs the examples under $(brew --prefix)/share/mont/example/.
```

This requires Python 3, FFmpeg/ffprobe, and a usable native GPU. It creates an
original synthetic terminal replay of actual CLI commands, not a desktop capture.
It checks initial/revised media timing, a native overlay edit, decoded frames,
and rendering a relocated bundle while the original workspace and recording are
unavailable. The new output directory retains seven MP4s, decoded proof images,
editable projects, command transcripts, and `report.json`.
Measured FFmpeg comparison timings are not a performance advantage claim.

## Friend usability check

After installing, confirm `mont --version` reports `mont 0.1.0-preview.2`, then:

```sh
python3 "$(brew --prefix)/share/mont/example/video_workflow.py" \
  --mont "$(brew --prefix mont)/bin/mont" \
  --output "$HOME/Downloads/mont-video-proof-001"
```

Use a new output directory. Watch the initial/revised MP4s, inspect the retained
JSON project snapshots and editable CLI workspace, then try importing a short
clip of your own. Report installation friction,
whether the revision/portable project made sense, time to a usable MP4, and exact
failures. Redact credentials and private paths; share media/projects only with
permission. A friend completing this is usability evidence, not acquisition or
independent demand.

## Private maintainer source builds

This section is for maintainers with access to the private monorepo, not an
installation requirement or an offer of source/SDK distribution. The Rust/clap
CLI calls `mont-workspace`; native state and synchronization live in the SDK.

From the repository root:

```sh
make mont-cli build
make mont-cli test
make mont-cli run ARGS='--help'
make mont-cli install
```

The Makefile uses Rust 1.91.0 and limits build concurrency. Source builds require
Xcode's native C/C++ toolchain, CMake, pkg-config, and Protocol Buffers tooling.
Media commands additionally require a usable native GPU adapter and external
`ffmpeg`/`ffprobe` executables. Rendering uses the existing native timeline/GPU
renderer and its external FFmpeg encoder. These source build tools are not
installation requirements for the packaged binary. `install` puts `mont` in
Cargo's binary directory. Git is optional and only required by `init --git`.

`make mont-cli test` runs nine real binary subprocess scenarios covering every
command, including local video/audio ingestion, rendering, portable recovery,
failure atomicity, and authenticated synchronization against an isolated HTTP
fixture. It also runs SDK, native media, release-security, and example checks.
The API's real authentication, authorization, immutable blob verification, and
compare-and-swap handlers are covered separately:

```sh
make api-server test-unit
# Focused checkpoint handler check, from projects/api-server:
go test ./handlers -run TestNativeCheckpoint -count=1
```

The [video/revision comparison](example/VIDEO-WORKFLOW.md) exercises Mont against
FFmpeg with identical input media and revision brief:

```sh
make mont-cli video-example OUTPUT=/absolute/new/directory/outside/checkout
```

It creates editable workspaces, initial/revised MP4s, a relocated bundle, command
transcripts, decoded-frame checks, and measured timings. It is a reproducibility
check, not evidence of independent demand or a controlled performance benchmark.

## Private release operator runbook

Release tooling requires Python 3.11+ on an ARM64 Mac. Run `make mont-cli ...`
from the repository root; run `python3 tool/...` from `projects/mont-cli`.
Keep CI, source, SDK, build logs, credentials, and unsigned artifacts private.
Use a tested native binary whose `mont --version` exactly matches `VERSION`;
the release version and Cargo package version must agree. Set `BINARY` to its
absolute path and `RELEASE_DIR` to a fresh absolute output directory.

`make mont-cli release-build` builds the optimized ARM64 candidate.
`make mont-cli package-internal` creates the internal package;
`make mont-cli package` is the gated signed path. The manual GitHub Actions
release workflow is dispatch-only on `master`: the default internal job can
create a private source-repository draft tagged `mont-cli-vVERSION-internal`.
It is not a public release. Public distribution repositories contain only
approved signed assets and public documentation, never private Mont source.

The video package declares external `ffmpeg` and `ffprobe` runtime executables;
it does not distribute their binaries or link a non-system FFmpeg/media stack.
Packaging audits the resolved Cargo/native dependency and embedded-font closure,
includes applicable third-party license notices and an SBOM, and rejects
unresolved licenses, private build paths, and non-system linked libraries.
External-process support does not waive these gates. Never replace preview.1
assets or publish an unsigned developer build as a released preview.

The audit records each declared SPDX expression and the selected distribution
license: `OR` elects an allowed alternative; `AND` retains both obligations.
The private checksum-pinned audit inputs retain original embedded-font OFL
notices, canonical MIT grants and original source notices for crates missing
standalone legal files, and the reviewed CC0 declaration/full license for
`hexf-parse 0.2.1`. These exceptions do not approve changed bytes or other packages.
Font licenses apply to the unmodified embedded fonts, not to proprietary Mont.

For the public Actions job, configure environment `mont-cli-public` with
required reviewers. Its secrets are `MONT_CLI_SIGNING_P12_BASE64`,
`MONT_CLI_SIGNING_P12_PASSWORD`, `MONT_CLI_SIGNING_IDENTITY`,
`MONT_CLI_NOTARY_KEY_BASE64`, `MONT_CLI_NOTARY_KEY_ID`,
`MONT_CLI_NOTARY_ISSUER_ID`, `MONT_CLI_PREVIEW_TERMS`, and
`MONT_CLI_RELEASE_TOKEN`. Pin the legally approved terms with repository
variable `MONT_CLI_PREVIEW_TERMS_SHA256`; the release token must have contents
write access only to the binary release repository and tap, never source
publication rights. Do not configure these with development certificates,
unapproved terms, or broader source-repository publishing credentials.

1. **Internal evaluation only:** `--unsigned` explicitly labels the package and
   manifest internal-only and records source dirtiness. Its ARM64 ad-hoc seal
   is not Developer ID signing, notarization, or a way around a release gate:

   ```sh
   python3 tool/release.py package --binary "$BINARY" \
     --output "$RELEASE_DIR" --version "$VERSION" --unsigned
   ```

2. **Public candidate:** first obtain approved proprietary preview terms, an
   actual **Developer ID Application** identity, and a working `notarytool`
   keychain profile. Set `APPROVED_TERMS` to the approved UTF-8 terms file,
   `DEVELOPER_ID_APPLICATION` to that identity, and `NOTARY_PROFILE` to the
   profile name. Public packaging requires a clean source checkout and the
   same legally approved `MONT_CLI_PREVIEW_TERMS_SHA256` exported in the local
   environment. Use a different fresh `RELEASE_DIR`:

   ```sh
   python3 tool/release.py package --binary "$BINARY" \
     --output "$RELEASE_DIR" --version "$VERSION" \
     --identity "$DEVELOPER_ID_APPLICATION" \
     --notary-profile "$NOTARY_PROFILE" --terms "$APPROVED_TERMS"
   python3 tool/release.py verify \
     --archive "$RELEASE_DIR/mont-$VERSION-darwin-arm64.tar.gz" \
     --require-signed --extract "$RELEASE_DIR/verified"
   ```

   The signed path must pass Developer ID signature verification and real
   Apple notarization with status `Accepted`. There is no simulated approval.
   The packager audits the CLI's resolved third-party dependency closure and
   bundles notices and an SBOM; unresolved licenses block public packaging.
   Source and build inputs are not release assets.

   Optional `--keychain /private/signing.keychain-db` selects the signing
   identity and notarization profile. macOS still requires that keychain in the
   user's search list: append it without replacing existing entries or changing
   the default, then delete only the temporary keychain after release. The
   packager does not mutate that list. The Makefile equivalent is
   `SIGNING_KEYCHAIN=/private/signing.keychain-db`.

3. **Acceptance before upload:** run
   `make mont-cli verify-package PACKAGE=/absolute/path/to/archive.tar.gz`
   from the repository root. It exercises all nine executable command scenarios
   with `MONT_TEST_BINARY` pointing to the verified extracted binary, then runs
   both bundled examples with isolated HOME/PATH and no SDK or Rust available.
   Supply `FFMPEG=/absolute/path/to/ffmpeg` and `FFPROBE=/absolute/path/to/ffprobe`
   if they are not on the operator's normal PATH. The video example decodes and
   checks initial/revised/portable MP4s; compilation or queued work is not proof.
   Verify SHA-256 checksums, native architecture, exact version, Apple signature,
   and notarization using `codesign -R=notarized --check-notarization`.
   `spctl --type execute` assesses app bundles, not this standalone executable;
   see [Apple's testing guidance](https://developer.apple.com/forums/thread/130560).
   Confirm no monorepo/SDK source, credentials, absolute private build paths,
   or unsupported rendering/interop claims appear in the payload.

4. **Publication:** only after legal/signing gates and acceptance, publish the
   archive, `SHA256SUMS`, and `release.json` to the binary-only
   `usemont/mont-cli` repository under tag `v$VERSION`. Never make the source
   repository public. Keep a candidate draft/private until approved; unsigned
   artifacts must never be attached to a public release.

5. **Homebrew:** after the signed release asset is publicly downloadable,
   generate a new formula from the actual verified archive and manifest:

   ```sh
   python3 tool/homebrew.py --manifest "$RELEASE_DIR/release.json" \
     --url "https://github.com/usemont/mont-cli/releases/download/v$VERSION/mont-$VERSION-darwin-arm64.tar.gz" \
     --output "$RELEASE_DIR/mont.rb"
   ```

   The generator re-verifies the local signed archive, rejects internal-only
   metadata, downloads the public asset without credentials, and requires the
   same SHA-256. It does not invent a URL or digest, publish a tap, or overwrite
   an existing formula. The public workflow publishes the tap update, then
   refreshes the tap and installs, upgrades, or reinstalls the exact release,
   checks the installed `mont --version`, then runs `brew test usemont/tap/mont`
   including the video proof. Publication is not accepted until these pass.

`release.py verify --archive` requires the adjacent `release.json` and
`SHA256SUMS`. It prints a JSON report containing the verified manifest;
`--extract` additionally returns the extracted root and binary paths. Extraction
must target a new directory. For an internal archive omit `--require-signed`;
the result remains internal-only, regardless of whether offline checks pass.
The Homebrew generator's offline rejection check is
`python3 tool/homebrew.py --self-test`; it does not establish public acceptance.

### Dispatch and public repository setup

The default workflow creates only a private, unsigned evaluation draft:

```sh
gh workflow run mont-cli-release.yml --ref master -f distribution=internal
```

After a signed package and terms approval exist, the repository owner provisions
the two **binary/docs-only** public repositories once (never change the private
monorepo's visibility):

```sh
gh repo create usemont/mont-cli --public --add-readme \
  --description 'Mont CLI Developer Preview — proprietary binaries and docs'
gh repo create usemont/homebrew-tap --public --add-readme \
  --description 'Homebrew tap for the Mont CLI Developer Preview'
```

Configure the restricted release token, signing secrets, approved terms hash,
and required reviewers described above. Then dispatch the public job:

```sh
gh workflow run mont-cli-release.yml --ref master -f distribution=public
```

The workflow checks prerequisites before building, verifies every command against
the extracted signed executable, and invokes `tool/publish.py`. The publisher
rechecks Apple trust and the exact packaged terms hash before any GitHub mutation.
It uploads binary archives, integrity metadata, and documentation only; no SDK
or monorepo source is uploaded. Released versions are immutable: bump the Cargo
version for another release rather than replacing published assets.


## Offline workspace

```sh
mont init ./movie --project-id my-project --git
mont -C ./movie status
mont -C ./movie validate
mont -C ./movie dispatch ./edit.json
mont -C ./movie diff
mont -C ./movie history
mont -C ./movie asset add ./original.png
mont -C ./movie export --bundle ./offline-copy
```

`init` creates a stable native project identity; omit `--project-id` to generate
one. It does not create a cloud project. `--git` initializes an optional repository
and ignores private `.mont/` state and the original-asset cache.

`dispatch` accepts the native SDK JSON command envelope from a file or `-` for
stdin. For example:

```json
{
  "commandId": "set-title-001",
  "command": {
    "type": "setProjectValue",
    "pointer": "/title",
    "value": "My movie"
  }
}
```

Use a distinct `commandId` for each intentional edit: native commands are
idempotent by ID. Undo/redo are native commands, not a separate CLI history.
Direct edits to `project.json` are validated and incorporated as native
transactions by subsequent workspace operations; invalid edits are rejected.

`-C` selects the workspace, but does not change the process working directory.
Other path arguments remain relative to the invoking directory. `init` and
`clone` take an explicit destination rather than using `-C`.

### Public files and private state

- `mont.json`: portable project identity, duration, and authored spans.
- `project.json`: the native project payload, editable as JSON.
- `assets/manifest.json`: object paths and SHA-256 pins.
- `assets/local/`: immutable original bytes, addressed by hash.
- `.mont/`: native session/history, synchronized base, cloud revision/origin,
  lock, and crash-recovery journal. No authentication token is persisted.

A checkout containing the public files can reopen without `.mont/`; the SDK
reconstructs a fresh local session while retaining project identity. Undo/redo
history and cloud ancestry are not reconstructed. Missing originals remain
explicitly unavailable. An existing damaged `.mont/` is not silently reset.

`status` and `diff` do not contact the cloud. `status` reports local changes and
missing originals. `validate` verifies native state and every pinned original.
`asset add` copies and hashes an original and registers its native resource; it
does not decode media or place a clip on the timeline.

`export --bundle` creates a **complete relocatable workspace directory, not an
MP4**. All pinned originals must exist and pass hash verification. It refuses
incomplete/corrupt projects and existing destinations, and publishes the bundle
only after verification. The bundle can reopen after its source is deleted.

### Media import and MP4 rendering

```sh
mont -C ./movie --json import ./recording.mp4
mont -C ./movie --json render --output ./initial.mp4 --fps 30
# Apply a native user_intent:programmatic_span.replace envelope from revision.json.
mont -C ./movie --json dispatch ./revision.json
mont -C ./movie --json render --output ./revised.mp4 --fps 30
```

`import` decodes a nonempty local video or audio file through SDK media ingestion,
pins its chunked originals, and appends an editable native span after existing
spans. Video starts with a centered contain-fit frame. The result includes
`assetId`, `sourceId`, `spanId`, placement/source timing, dimensions, and native
`projectRevision`. `asset add` remains registration only.

`render` exports the current native project through the canonical timeline/GPU
renderer. Its result includes output path, dimensions, duration, frame count,
frame rate, project revision, and `audio.present`. Embedded video audio and
audio-only spans follow their source trim, timeline placement, rate, and volume.
Use an existing parent directory and a **new `.mp4` output filename**; existing
files are never overwritten. Frame rate must be finite and within 1–240; scene
dimensions must be positive even integers.

Media source trims and playback rate must agree with the native clip duration.
Static destination frames and crops are supported; rotated media, animated or
spatial transforms, external asset bindings, unsupported capabilities, missing
originals, and renderer-skipped clips fail explicitly rather than producing a
silently incomplete movie. Rendering holds the workspace lock and verifies pins.
The portable bundle retains media bytes, so reopening/rendering does not require
the original import path. The bundled video example contains complete native
revision JSON; the comparison runbook explains the checks and limitations.

## Explicit cloud synchronization

The API deployment must support Mont's native checkpoint endpoints (maintainers:
see the private API server's Swagger documentation).
The cloud project must already exist, with the same project ID as the workspace.
The caller needs read permission for cloning/pulling and write permission for
publishing originals/checkpoints. Set `MONT_TOKEN` privately in the environment;
never put it in a project file or command argument.

```sh
mont clone PROJECT_ID ./checkout --remote https://api.example.com
mont -C ./checkout pull
mont -C ./checkout push --dry-run
mont -C ./checkout push
mont -C ./checkout sync
```

The remote is an API origin, without a path/query/credentials. HTTPS is required,
except loopback HTTP for local development. The first call requires `--remote`;
successful synchronization remembers only the origin privately.

| Command | Behavior |
| --- | --- |
| `clone PROJECT_ID PATH` | Read an authenticated checkpoint into a new workspace. |
| `pull` | Three-way merge remote changes into the workspace, without publishing. |
| `push` | Publish local changes only against the recorded cloud revision. A stale cloud revision is refused. |
| `sync` | Three-way merge, then publish the combined checkpoint with revision checking. |

All four commands accept `--remote`, `--dry-run`, and `--assets-all`. Originals
are lazy on normal clone/pull; `--assets-all` downloads and verifies them.
Publishing always verifies every pinned original and uploads missing bytes
before advancing the cloud checkpoint. A CLI invocation never silently retries
a stale revision by overwriting the cloud.

Independent changes merge; overlapping edits and incompatible collection edits
are conflicts. With no common synchronized base, an existing remote checkpoint
cannot be overwritten by an unrelated local workspace. Conflicts/stale revisions
leave the local project, synchronized base, and cloud checkpoint unchanged.

Dry runs may read the remote and verify downloaded bytes, but do not write local
state, upload originals, or publish checkpoints. A checkout needing local
recovery must run `status` before a dry run; the dry run itself does not create
or repair `.mont/`.

The server limits checkpoint requests to 16 MiB and each original to 256 MiB;
deployment ingress may impose a lower limit. Server storage requires GCS and
Firestore configuration described in the endpoint documentation.

**Interop boundary:** these endpoints store native checkpoints separately from
legacy `projectData`/`stateRevision`. Existing Flutter/mobile cloud persistence
has not been migrated to this checkpoint protocol. A CLI → phone → CLI cloud
roundtrip is therefore not provided by this change. Production deployment and
live device acceptance are separate prerequisites; local HTTP tests do not
establish either.

## Output contract

`--json` is global and emits one stdout object:

```json
{"ok":true,"result":{}}
```

Failures emit `{"ok":false,"error":{"code":"...","message":"..."}}` on stdout,
without stderr output. Human mode prints formatted results and sends errors to
stderr. Help/version retain clap's normal text output.

| Exit | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Other runtime/I/O/workspace failure |
| 2 | Invalid arguments or input |
| 3 | Merge conflict or stale revision |
| 4 | Missing/corrupt original or other integrity failure |
| 5 | Authentication/authorization denied |
