# Package Daddy package format

Package Daddy only installs a ZIP with a `package_daddy.package.json` manifest. The
manifest may be at the archive root or inside one wrapper directory. Everything else
is discovery-only until a future approved remote catalog supplies the same manifest.

```json
{
  "format": 2,
  "id": "example-plugin",
  "name": "Example Plugin",
  "kind": "mo2-plugin",
  "version": "1.2.3",
  "install_root": "example-plugin",
  "files": [
    "__init__.py",
    "example_plugin.module.json",
    "package_daddy.package.json"
  ]
}
```

Rules:

- `id` is lowercase letters, digits, `_`, or `-` and is the stable package identity.
- `kind` is `mo2-plugin`, `mo2-theme`, `mo2-bridge`, or `mo2-tool`.
- `install_root` is one new owned folder. Plugins/bridges go below MO2's `plugins`
  directory; tools go below MO2's `tools` directory. It may not be an absolute
  path, contain `..`, or name a nested directory.
- Every `files` entry is relative to `install_root`, is validated before installation,
  and is the *only* material extracted.
- The destination directory must not already exist. Package Daddy never overwrites,
  merges into, or upgrades an existing package.
- A completed install is recorded in `package_daddy_installs.json` beside Package
  Daddy. If the receipt cannot be written, the just-created destination is removed.

Version 1 manifests were scan-only prototypes and are intentionally not installable.

## Theme manifest

For an MO2 stylesheet bundle, the author can put this small file at the ZIP
root. Theme Daddy then identifies the package without depending on its archive
name. Theme assets are reviewed separately: only files below a `stylesheets/`
folder with ordinary stylesheet/image/font extensions are offered for install.

```json
{
  "format": 2,
  "id": "skyrim-factions-mo2-themes",
  "name": "Skyrim Factions MO2 Themes",
  "kind": "mo2-theme",
  "version": "2.4",
  "install_root": "stylesheets",
  "files": ["package_daddy.package.json"],
  "nexus_url": "https://www.nexusmods.com/skyrimspecialedition/mods/193010"
}
```

`install_root` is retained for the common manifest shape; Theme Daddy does not
extract a theme into MO2's `plugins` folder. It presents the archive's reviewed
stylesheet payloads (including FOMOD-style choices) and writes only the options
the user selects below MO2's real `stylesheets` folder.

## Tool manifest

Tools use the same small declared-file list. Tool Daddy creates MO2's `tools`
folder only after the user confirms an install, then stages the archive and
moves one owned folder into place. It never scatters a tool into some mystery
directory elsewhere on the drive.

```json
{
  "format": 2,
  "id": "example-tool",
  "name": "Example Tool",
  "kind": "mo2-tool",
  "version": "1.0.0",
  "install_root": "Example Tool",
  "files": [
    "ExampleTool.exe",
    "readme.txt",
    "package_daddy.package.json"
  ]
}
```

Only declared tool packages can install for now. A catalog name match may help
Daddy recognize a download, but it never earns permission to unpack an unknown
tool archive.
