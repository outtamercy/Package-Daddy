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

## Compatibility is two separate questions

Package Daddy records an optional ISO `release_date` and a compatibility
signal for every catalog entry. This lets the catalog flag a release as
`likely-current` when it was published on or after the MO2 2.5.2 baseline.
That is useful triage: a plugin released last week deserves a look before one
released years ago. It is **not** a promise that it loads.

The important hard boundary is MO2 2.5.0, which moved extensions to Qt6 and
Python 3.11. Old PyQt5-era packages are `unknown` until reviewed and should
never become installable just because their release date is recent-looking.

Use one of these values in `compatibility`:

- `verified-current` — actually checked on the stated current MO2 baseline.
- `likely-current` — recent enough to be a sensible review candidate; not yet
  verified or installable.
- `unknown` — no compatibility conclusion has been made.
- `known-incompatible` — do not offer it as an install target; include why.

Add `release_date` as `YYYY-MM-DD` only when it comes from the project or
release source. No guessing dates from archive filenames.

### PF-style release record

Once a maintainer has checked a release, put the compatibility fact on that
release rather than making a vague claim about the whole project:

```json
{
  "version": "1.4.0",
  "released": "2026-10-09",
  "min_working": "2.5.2",
  "max_working": "",
  "min_support": "2.5.2",
  "max_support": ""
}
```

`min_working` / `max_working` drive Daddy's compatibility decision. The
support range is shown separately: something can be known to work without the
author officially supporting that MO2 version. If several release records fit,
Daddy selects the highest plugin version in the working range.

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
