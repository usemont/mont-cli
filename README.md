<p><a href="https://usemont.com"><img src="https://raw.githubusercontent.com/usemont/.github/main/assets/logo.svg" alt="Mont" width="150" height="40"></a></p>

# Mont CLI

Create editable native video projects and render MP4s from reusable workflows with Mont's command-line video editor.

<table>
  <tr>
    <td width="33%" align="center"><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/stretch-row-repeater"><img src="https://raw.githubusercontent.com/usemont/workflows/main/assets/previews/stretch-row-repeater-square.gif" alt="Stretch Row Repeater native Mont render" width="260"></a><br><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/stretch-row-repeater">Stretch Row Repeater</a></td>
    <td width="33%" align="center"><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/letter-inflate-swell"><img src="https://raw.githubusercontent.com/usemont/workflows/main/assets/previews/letter-inflate-swell-square.gif" alt="Letter Inflate Swell native Mont render" width="260"></a><br><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/letter-inflate-swell">Letter Inflate Swell</a></td>
    <td width="33%" align="center"><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/spiral-type"><img src="https://raw.githubusercontent.com/usemont/workflows/main/assets/previews/spiral-type-square.gif" alt="Spiral Type native Mont render" width="260"></a><br><a href="https://github.com/usemont/workflows/tree/main/templates/kinetic/spiral-type">Spiral Type</a></td>
  </tr>
</table>

Native Mont renders · editable projects + MP4 output · landscape, portrait, and square templates.

## Try a template

**macOS 14+ · Apple Silicon · Node.js 22+**

```sh
npx usemont templates preview morph-text
# Reproducible CI:
npx --yes usemont@0.1.0-preview.17 templates preview morph-text
```

No Homebrew CLI install is required. Preview and the bundled editor need no
FFmpeg; media import/rendering still need external FFmpeg/ffprobe.
Keep npm optional dependencies enabled.

The default npm channel currently selects `0.1.0-preview.17`, a preview release,
not a stable release. Pin an exact version for reproducible CI.

Alternatively, `brew install usemont/tap/usemont` installs the native `mont`
executable. npm and Homebrew update independently. Existing `@usemont/cli`
installs require explicit package-name migration; project lockfiles stay pinned.

**Linux:** [signed APT preview](https://github.com/usemont/mont-cli/blob/main/docs/install.md#linux); TSX-only templates require the macOS release for now.
[More templates](https://github.com/usemont/workflows#readme) · [Installation and updates](https://github.com/usemont/mont-cli/blob/main/docs/install.md).
Windows is not available.

## Edit locally

```sh
mont templates create stretch-row-repeater ./my-video
mont edit ./my-video
# Export from the browser, or:
mont -C ./my-video render --output ./my-video.mp4
```

The bundled web editor saves scene inputs back to TSX and project edits to the
local workspace. Keep the command running while editing.

## Optional account

```sh
mont auth login
mont auth status
mont auth logout
```

The account icon in `mont edit` shares the CLI session. Local editing and export
need no account; credentials stay in the operating system's credential store.

Output is readable by default. Add `--json` for agents, scripts, or `jq`.

## Published workflows and local runs

```sh
mont workflows list
mont workflows show explainer
mont workflows run explainer --inputs inputs.json --config providers.json --run-id demo
mont workflows runs
mont workflows status demo
mont workflows resume demo
mont workflows cancel demo
```

`list` discovers the published catalog; `runs` reads local persisted history.
`run` also accepts an existing local file or explicit path such as `./recipe.mont`.
A missing local path fails without falling back to the network. See the
[explainer workflow](https://github.com/usemont/workflows/tree/main/workflows/explainer)
for required inputs and provider endpoint/model/environment-key configuration;
never place raw API keys in config files.

Catalog discovery and published execution fetch the official repository freshly,
pin assets to one commit, and fail explicitly when offline. New catalog entries
require no binary rebuild. Downloaded workflow sources are retained under
`out/<name>-sources/`; persisted local runs can be inspected/resumed offline.

[Releases](https://github.com/usemont/mont-cli/releases) · [Report a bug](https://github.com/usemont/mont-cli/issues) · [Security](https://github.com/usemont/mont-cli/security/advisories/new) · [Agent skills](https://github.com/usemont/skills)

Mont CLI is proprietary software by **Nauchu AB**; release `TERMS.txt` applies. [Workflows](https://github.com/usemont/workflows) and [skill source](https://github.com/usemont/skills) are Apache-2.0.
