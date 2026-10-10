# Package Daddy catalog

This folder is the public, curated directory used by Catalog Daddy. It is not
a crawler and it is not a promise that every project named here is safe to
install.

The [official MO2 extension-plugin wiki](https://github.com/ModOrganizer2/modorganizer/wiki/List-of-MO2-Extension-Plugins)
is the starting index for Python plugins. Its entries give us a project name
and public project links; they do **not** prove that a release is current,
compatible with the user's MO2/Python/Qt version, or safely packaged.

## Entry states

- `listed` — known project with links. Catalog Daddy can show it and open its
  links, but cannot download or install it.
- `verified` — a maintainer has checked the project against current MO2 and
  recorded useful compatibility information. It is still not installable
  unless it has an `install` block.
- `installable` — has a reviewed direct archive source and an explicit
  packaging rule. This is the only state Catalog Daddy may download/install.
- `blocked` — retained so users can see why it is not offered. Include a
  human-readable `reason`.

## Add an installable package

Do not turn a `listed` entry into `installable` just because its filename
looks plausible. First verify all of these:

1. The release URL is an author-controlled direct archive, preferably a
   GitHub/GitLab release asset.
2. The archive has been inspected without executing it.
3. Its install root and file list are known and use no path traversal.
4. The package works on the MO2/Python/Qt versions stated in `compatibility`.
5. The package's author and support links are accurate.

Nexus links are welcome metadata, but a normal Nexus page is not a direct
download URL and must not be used as one.
