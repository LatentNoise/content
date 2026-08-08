# Contributing

Content follows a **single-maintainer development model**. Bug reports, feature
requests and design feedback are welcome. **Code contributions are not
accepted** — pull requests are turned off in the repository's settings, so
there is no way to open one.

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
| 🔀 **Pull requests** | **No.** They are disabled in the repository's settings, so the button is not there to press. Forking is the intended path instead. |
| 👥 **Collaborator access** | **No.** There is one maintainer, by design — see [GOVERNANCE.md](GOVERNANCE.md). |

## Why code contributions are refused

Two reasons. The second is the one that settles it.

**Maintenance.** Reviewing a change properly — understanding the intent,
checking it against the invariants, testing it, and then owning it for as long
as the project lives — costs more than writing it. Half-reviewing someone else's
code into a project I depend on is worse than not taking it at all.

**Copyright.** The moment outside code is merged, its author holds copyright in
it. Content is published under the AGPL *and* may be offered under separate
commercial terms ([COMMERCIAL.md](COMMERCIAL.md)) — and offering those terms
requires holding all the rights. A single merged pull request would end that
permanently, unless every contributor could be found again and asked to sign
their rights away. Keeping the copyright undivided is what keeps that option
open, which is why there is no CLA either: there is nothing to sign, because
nothing is accepted.

## What you can do instead

**Fork it.** The AGPL grants you that right explicitly, and it is the intended
path if you want Content to behave differently. Fork it, change it, run it. You
need no permission and you do not have to tell anyone.

Two things to know if you do: if you offer your *modified* version to others
over a network, the AGPL requires you to make its source available to them (that
obligation applies to this project too — see `CONTENT_SOURCE_URL` in
[docs/operations/deployment.md](docs/operations/deployment.md)); and a fork is an
independent project that must not present itself as official.

## Policy vs. enforcement

Being precise about which of these is a rule and which is a wall, because the
difference matters:

| | |
| --- | --- |
| **Enforced by the platform** | Pull requests are disabled in the repository's settings, so none can be opened. Merge rights, releases and package publication are restricted to the maintainer. |
| **Stated here as policy** | That no collaborator will be added, and that the roadmap is a personal one. Issues are open, and moderated by the maintainer alone. |

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
