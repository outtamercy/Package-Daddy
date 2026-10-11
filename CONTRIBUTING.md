# Contributing to the Package Daddy Catalog

Hell yeah, help is welcome. This catalog either grows with the MO2
community or it turns into some tiny private list only I can fix, and
nobody wants that.

Before opening a PR, read [catalog/README.md](catalog/README.md). The short
version: give us real project links and real release dates; do not guess; and
do not mark something installable because its ZIP has a reassuring name.

## Fastest way to help

Open an issue if you found:

- a missing MO2 plugin, tool, or theme;
- an outdated Nexus, GitHub, Discord, wiki, or project link;
- a newer release/version/date;
- a compatibility correction for a specific MO2 version; or
- a catalog row that is plain wrong.

Include the project's official page, its current version and date if known,
and what MO2 version you tested. Screenshots are handy, but a link we can
verify is better.

## Sending a pull request

1. Fork this repository.
2. Edit `catalog/package_daddy_catalog.json`.
3. Keep the JSON valid and use the existing field names/style.
4. Explain where each new link, version, date, and compatibility claim came
   from in the PR description.
5. Keep unrelated reformatting out of the change. It makes review suck.

Small correction? Sweet. One focused PR is perfect.

## Safety rules, because apparently we need them

- No direct downloads from random mirrors, file hosts, or link shorteners.
- A Nexus page is metadata, not a blessing. "It's on Nexus" is not proof a
  file is safe to auto-install.
- `installable` requires a maintainer review of the actual archive and its
  packaging rules. Community submissions begin as `listed` or `verified`.
- Do not use archive filenames as release dates. They lie. A lot.
- Do not include API keys, tokens, passwords, or private URLs. Ever.

We review everything before merging. Not because we don't trust you —
because that's how a community catalog doesn't turn into a community
malware buffet.
