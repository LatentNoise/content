# ADR 0021 — A job does not succeed while a step failed

Status: proposed (2026-08-15) · Refines the terminal-status rule of
`docs/domain.md` §4 · Prompted by collections (ADR 0019) and by a delivery
failure found in testing

## Context

`aggregate_final_status` decides a job's terminal status from **whether each
output produced at least one artifact**. Step outcomes are not an input:

```python
if required_missing: return "failed"
return "partially_succeeded" if optional_missing else "succeeded"
```

That rule is defensible in isolation — the caller asked for outputs, and the
outputs exist — but it produces answers that are wrong in the way that matters:
the user is told everything worked, and something did not.

**Two cases, both real, both observed rather than imagined.**

*A collection with a failed member.* A playlist of six where one video is
audio-only and cannot satisfy a `video` output: five artifacts are produced, the
output is non-empty, and the job reports **`succeeded`**. The per-member truth
exists only as a `step.failed` event, which no UI surfaces and no exit code
reflects. A user who asked for six videos and received five is told nothing.
This is pinned today by `test_one_incapable_member_does_not_spoil_the_others`,
which asserts `succeeded` — that assertion is the behaviour under review.

*A delivery that fails.* Testing the Linux ownership matrix produced this
exactly: a media library owned by another uid, so the copy into it is refused.
The step reports
`delivery_failed: could not deliver artifact: [Errno 13] Permission denied`,
the artifact exists in the job's store, `produced_count` is incremented **before**
delivery is attempted — and the job reports `succeeded`. The operator sees a
green job and an empty library. This is the more damning case, because nothing
about it is a collection or an edge: it is what every Linux user with a NAS
library gets.

The contract already owns the right word. `partially_succeeded` exists, is
documented, and every client already has to handle it.

## Decision

**A job whose terminal status would be `succeeded`, but in which at least one
step ended `failed`, reports `partially_succeeded` instead.**

Stated as the rule it really is: *`succeeded` means everything asked for
happened.* Nothing else changes — `failed` still means a required output is
missing, and a job with no failed step and no missing output still succeeds.

Concretely, `aggregate_final_status` gains one input, `any_step_failed`, and one
clause: it can no longer return `succeeded` when that is true.

### How it composes with what already exists

- **`failure_policy`.** Unchanged in its own right. `required_only` and
  `fail_fast` still fail on a missing required output; `best_effort` still
  refuses to fail while any artifact was produced. The new clause only ever
  moves an answer from `succeeded` to `partially_succeeded`, never from `failed`
  to something softer.
- **`required` on an output.** Unchanged. A failed step under an optional output
  already tended to produce `partially_succeeded` through `optional_missing`;
  now it does so even when a sibling step covered for it.
- **`fail_fast` under member concurrency.** Already decided in ADR 0019 —
  members in flight finish, queued ones are skipped. Those skipped steps are
  `skipped`, not `failed`, so they do not by themselves demote the status; the
  member that actually failed does.
- **The `partial_output` warnings** that planning already emits are unaffected:
  they describe what was planned, this describes what happened.
- **Retry.** Out of scope, and said plainly rather than implied: retrying a
  partially succeeded job re-runs the **whole** plan today. Re-running only the
  failed members is a genuine feature and is not part of this decision.

### What every client owes

A status nobody surfaces is not an improvement:

- **CLI `--watch` must not exit 0 on `partially_succeeded`.** A script that
  chains on success must not proceed as if all six videos arrived. A distinct
  non-zero code, documented.
- **The three UIs** render it distinctly from success — HomeTube and Studio in
  the job panel, the Console in its list — with the failed steps' reasons
  reachable, since the reason is already in the events.
- **MCP** reports it in `get_job` alongside the failures list it already builds.

## Consequences

**Gained.** The status stops lying in the two cases that occur most: a playlist
that partly worked, and a delivery that silently did not. Both are exactly the
situations where a user would otherwise discover the gap by browsing their
library days later.

**Paid.** Jobs that report `succeeded` today will report `partially_succeeded`
tomorrow — the same runs, a different word. That is a **behaviour change on a
published contract**, and it is the reason this is an ADR and not a bug fix. It
needs a release note that says plainly: if you keyed automation on
`status == "succeeded"`, a job with a failed delivery now answers differently,
and that is the point.

`test_one_incapable_member_does_not_spoil_the_others` changes its assertion, and
its name stops being quite right — the members are still not spoiled, but the
job now admits one of them failed.

**Not decided here.** Whether delivery failure should fail the step at all. One
could argue the artifact *was* produced and delivery is a copy, so the step
succeeded with a warning. That is a coherent position, and it would fix the
symptom in the delivery case while leaving the collection case untouched. It is
rejected as the primary fix precisely because it is narrower: the collection
case needs this rule anyway, and two mechanisms for one question is worse than
one.
