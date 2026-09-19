# Contributing

Content follows a **single-maintainer development model**. Bug reports, feature
requests and design feedback are welcome. **Code contributions are not
accepted**: unsolicited pull requests are closed without review. Forking is the
intended path instead.

That is a deliberate design choice, not a temporary state and not a comment on
anyone's code.

---

## What is welcome, and what is not

| | |
| --- | --- |
| 🐛 **Bug reports** | **Yes.** The most useful thing you can send. Include the version, how you deployed it, steps to reproduce, and the relevant log lines. |
| 💡 **Ideas & feature requests** | **Yes.** They shape the roadmap. Opening one is not a commitment that it will be built. |
| 💬 **Design feedback** | **Yes.** Especially where the contract or the model feels wrong. |
| 🔒 **Security reports** | **Yes, privately.** Never in a public issue — see [SECURITY.md](SECURITY.md). |
| 🔀 **Pull requests** | **No.** GitHub lets anyone open one on a public repository, so the button exists — but unsolicited pull requests are closed without review. Forking is the intended path instead. |
| 👥 **Collaborator access** | **No.** There is one maintainer, by design — see [GOVERNANCE.md](GOVERNANCE.md). |

## Why code contributions are refused

Two reasons. The second is the one that settles it.

**Maintenance.** Reviewing a change properly — understanding the intent,
checking it against the invariants, testing it, and then owning it for as long
as the project lives — costs more than writing it. Half-reviewing someone else's
code into a project I depend on is worse than not taking it at all.

**Copyright.** The moment outside code is merged, its author holds copyright in
it — and only the holder of *all* the rights can change the licence or grant
terms other than the published ones ([COMMERCIAL.md](COMMERCIAL.md)). That is
not hypothetical here: Content moved from AGPL-3.0-or-later to FSL-1.1-ALv2 on
2026-09-18, and it could do so only because every line was its author's. A
single merged pull request would have ended that permanently, unless every
contributor could be found again and asked to sign their rights away.

So code contributions are **paused rather than refused on principle** — they
stay closed until a contributor agreement exists to assign or license those
rights back. There is none today, which is why there is nothing to sign and
nothing is accepted. Everything else — issues, bug reports, design feedback,
discussion — is welcome and always was.

## What you can do instead

**Fork it.** FSL-1.1-ALv2 grants you the right to modify Content and run your
version, and that is the intended path if you want it to behave differently.
Fork it, change it, run it — for yourself, or inside your organisation. You need
no permission, you do not have to tell anyone, and you are not required to
publish your changes.

Three things to know if you do. Your fork stays under the same licence, so it
may not be offered as a competing commercial product or service
([COMMERCIAL.md](COMMERCIAL.md)). It is an independent project that must not
present itself as official, or use the project's names for its own product. And
if you run it for others, please point `CONTENT_SOURCE_URL` at *your* source
rather than upstream (see
[docs/operations/deployment.md](docs/operations/deployment.md)) — the licence no
longer obliges it, but telling your users something untrue is worse than saying
nothing.

## Policy vs. enforcement

Being precise about which of these is a rule and which is a wall, because the
difference matters:

| | |
| --- | --- |
| **Enforced by the platform** | Merge rights, releases and package publication are restricted to the maintainer, so nothing lands without them. |
| **Stated here as policy** | That unsolicited pull requests are closed unread, that no collaborator will be added, and that the roadmap is a personal one. GitHub offers no way to disable pull requests on a public repository, so this one is a policy the maintainer applies, not a wall — please do not spend your time on a change that will be closed. Issues are open, and moderated by the maintainer alone. |

`CODEOWNERS` records ownership; it does not itself block anything. It exists so
that if contributions were ever accepted, review would still be required from
the maintainer — and that would additionally need a branch protection rule. The
settings this model assumes are listed in
[docs/operations/github-settings.md](docs/operations/github-settings.md).

## Conduct

Issues are open, so: be civil, stay on the technical point, and assume the
person reading has limited time. Abusive, discriminatory or bad-faith
participation gets the issue locked and the account blocked, without discussion.
There is no committee and no appeal — that is the whole policy, and a longer
document would not add anything given that participation is limited to issues.

## The project's priorities

If you are deciding whether to use or fork Content, these are the trade-offs it
actually makes:

- **Stability over features** — a thing that works beats a thing that does more.
- **Simplicity over completeness** — every abstraction must justify itself with
  a concrete need, not a hypothetical one.
- **Clarity over cleverness** — code is read far more often than written.
- **Security over convenience** — no secrets in the contract, no shell
  interpolation, no path the operator did not allow.
- **An honest contract over a generous one** — "valid but not implemented" is a
  different answer from "invalid", and both beat pretending.

These are set out properly in
[docs/architecture/invariants.md](docs/architecture/invariants.md) and
[docs/product/scope.md](docs/product/scope.md).
