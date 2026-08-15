# Privacy policy — HomeTube for Content (browser extension)

Last updated: 15 August 2026

## The short version

The extension sends the address of the page you are looking at to **your own
Content server**, and nothing else, to nobody else. Its author receives no data
of any kind.

## What is stored, and where

Your settings only: the address of your Content engine, which output type is
preselected, a default destination folder, and a quality ceiling. They are kept
in your browser's own storage (`chrome.storage.sync`), so they follow your
signed-in Chrome profile across your devices. They never leave that mechanism.

No browsing history, no page content, no personal data of any kind is stored.

## What is transmitted, to whom, and when

When you click the extension's icon and submit, the address of the current tab
is sent to the Content engine **you configured** — by default
`http://localhost:8010`, a server on your own computer, or an address on your
own network that you typed yourself.

That is the only network destination the extension ever contacts. It does not
send anything to the author, to any analytics service, or to any third party,
and it does not read the content of the pages you visit — only the tab's URL,
and only when you click.

## What is not done

- No analytics, no telemetry, no crash reporting.
- No account, no sign-in, no identifier of any kind.
- No sale or sharing of data — there is none to sell or share.
- No use of data for creditworthiness or lending purposes.
- No advertising.

## Permissions, in plain terms

- **activeTab** — lets the extension read the URL of the tab you are on, at the
  moment you click the icon, and only that tab.
- **storage** — keeps your settings.
- **Access to `localhost:8010`** — the default address of a Content engine
  running on your own machine.
- **Optional access to other addresses** — requested only if you type your own
  server's address into the settings and save it, and only for the address you
  typed. Content is self-hosted, so the server's address is one you choose; it
  cannot be known in advance.

## Children

The extension is not directed at children and collects nothing from anyone.

## Changes

Any change is published in this file, in the project's public repository, with
the date above updated. The repository's history is the record.

## Contact

Yann Orieult — <yann@orieult.com> ·
<https://github.com/LatentNoise/content/issues>
