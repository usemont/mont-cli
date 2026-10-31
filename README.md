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

```sh
# install
brew install usemont/tap/mont
# discover templates, inspect declared inputs, generate a native preview
mont templates list
mont templates show stretch-row-repeater
mont templates preview stretch-row-repeater
```

**Linux · Debian 12 arm64 / amd64:** [signed APT installation](docs/install.md#linux).
[More templates](https://github.com/usemont/workflows#readme) · [Installation and updates](docs/install.md).
Windows is not available.

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


[Releases](https://github.com/usemont/mont-cli/releases) · [Report a bug](https://github.com/usemont/mont-cli/issues) · [Security](https://github.com/usemont/mont-cli/security/advisories/new) · [Agent skills](https://github.com/usemont/skills)

Mont CLI is proprietary software by **Nauchu AB**; release `TERMS.txt` applies. [Workflows](https://github.com/usemont/workflows) and [skill source](https://github.com/usemont/skills) are Apache-2.0.
