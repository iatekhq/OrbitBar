# Security policy

OrbitBar runs on students' and other people's PCs, so security problems are taken seriously.

## Reporting a problem

**Please report it privately**, not in a public issue: go to the repo's **Security** tab → **Report a vulnerability** ([direct link](https://github.com/iatekhq/OrbitBar/security/advisories/new)). Only IATEK can see the report.

Please include what an attacker could do, the steps to reproduce it, and the OrbitBar version (right-click the bar → *Check for updates (you have …)*).

What to expect:

- A reply within **7 days**
- A fix in a new release as soon as possible, usually within **30 days** for serious problems. Everyone's bar installs it automatically.
- Credit in the release notes, if you'd like it

Please give us time to fix it before you talk about it publicly, and don't test against other people's computers.

## What counts

For example: anything that lets a website, a Wi-Fi network, a calendar feed, another program or a download run code, read files, or leak information through OrbitBar; a way to get a fake update installed; anything that hides the Windows taskbar for good.

Not a vulnerability by itself: something that needs an attacker who already runs programs as your Windows user (they could change any app at that point), or the quick launcher running what you type into it (that's its job).

## Supported versions

Only the latest release gets fixes. The bar updates itself, so you're on it unless you turned automatic updates off.

## How releases are protected

Builds are made by GitHub Actions from this repository, with every dependency pinned and hash-checked, and come with a signed build-provenance record. The updater only installs releases from this repository whose SHA-256 matches GitHub's. Details in the README (*Build it yourself* and *Official builds*).
