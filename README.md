# Package Daddy

Package Daddy is an MO2 plugin package manager. It discovers explicitly declared MO2 plugins, themes, and bridges from MO2's configured Downloads folder without scanning archive code or treating every ZIP with a Python file as an installer.

Current first cut: read-only ZIP discovery. A Package Daddy manifest identifies an installable package; older archives must have an MO2-plugin-shaped `__init__.py` package or root-level QSS/CSS theme before they appear as review-only candidates. Install/restart handling comes after the package contract is proved in MO2.

This is the public, community-maintained catalog that Package Daddy reads for MO2 plugins, tools, and themes. It's a directory, not a random-download machine: `listed` does not mean reviewed, and reviewed does not mean installable. Don't get those twisted.

Got a project to add, or a link/date gone stale? Open an issue or send a pull request. Read [the contribution guide](CONTRIBUTING.md) first, so none of us are playing twenty questions over a mystery ZIP at 2am.

The catalog itself and its field rules live in [catalog/](catalog/).
