# Package Daddy catalog

This folder is the public, curated directory that Catalog Daddy reads. It is
not a crawler, and it is not a promise that everything named here is safe to
install. Curated means a human looked at it. It does not mean a human blessed
it.

The [official MO2 extension-plugin wiki](https://github.com/ModOrganizer2/modorganizer/wiki/List-of-MO2-Extension-Plugins)
is our starting index for Python plugins. It gives us a project name and
public links. What it does **not** give us is proof that a release is current,
works on your MO2/Python/Qt combo, or was packaged by someone with functioning
braincells. That part is on us.

## Entry states

- `listed` — we know it exists and it has links. Catalog Daddy can show it
  and open its links. Download? No.
- `verified` — a maintainer actually checked it against current MO2 and
  wrote down what they found. Still not installable unless it has an
  `install` block.
- `installable` — reviewed direct archive source plus an explicit packaging
  rule. This is the only state Catalog Daddy may download/install. Everything
  else is a hard no.
- `blocked` — kept around so users can see why it's not offered. Include a
  human-readable `reason`.

## Compatibility is two separate questions

Package Daddy records an optional ISO `release_date` and a compatibility
signal for every catalog entry. That lets the catalog flag a release as
`likely-current` when it landed on or after the MO2 2.5.2 baseline. Useful
triage: a plugin released last week deserves a look before one released years
ago. It is **not** a promise that it loads.

The hard line is MO2 2.5.0, which moved extensions to Qt6 and Python 3.11.
Old PyQt5-era packages are `unknown` until reviewed and should never become
installable just because the date looks fresh. Dates lie. Filenames
especially.

Use one of these values in `compatibility`:

- `verified-current` — actually checked on the stated current MO2 baseline.
- `likely-current` — recent enough to be a sensible review candidate; not
  verified, not installable.
- `unknown` — nobody has drawn a conclusion yet. Say that, don't fake it.
- `known-incompatible` — do not offer it as an install target; include why.

Add `release_date` as `YYYY-MM-DD` only when it comes from the project or
release source. No guessing dates from archive filenames. They lie. A lot.

### PF-style release record

Once a maintainer has checked a release, pin the compatibility fact to that
release instead of making vague noises about the whole project:

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
support range is shown separately: something can work without the author
officially supporting that MO2 version. If several release records fit,
Daddy picks the highest plugin version in the working range.

## Add an installable package

Do not promote a `listed` entry to `installable` because the filename looks
plausible. Filenames look plausible for a living. First verify all of these:

1. The release URL is an author-controlled direct archive, preferably a
   GitHub/GitLab release asset.
2. The archive has been inspected without executing it.
3. Its install root and file list are known and use no path traversal.
4. The package works on the MO2/Python/Qt versions stated in `compatibility`.
5. The package's author and support links are accurate.

Nexus links are welcome metadata, but a normal Nexus page is not a direct
download URL and must not be used as one. Same rule as the contributing doc:
a Nexus page is not a blessing.
