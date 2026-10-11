# Package Daddy
packages misbehaving in downloads? Daddy puts them where they belong.

Package Daddy is an MO2 plugin package manager. It discovers explicitly
declared MO2 plugins, themes, and bridges from MO2's configured Downloads folder
without scanning archive code or treating every ZIP with a Python file as an
installer.

Current first cut: read-only ZIP discovery. A Package Daddy manifest identifies
an installable package; older archives must have an MO2-plugin-shaped
`__init__.py` package or root-level QSS/CSS theme before they appear as
review-only candidates. Install/restart handling comes after the package
contract is proved in MO2.
