# `.audit/` — what was checked before a merge happened

## `.audit/merges/`

One file per merge attempt, written by `scripts/merge-pr-with-codeant`
**before** the merge runs, never after.

```
.audit/merges/pr-<PR>-<HEAD>.json
```

`<HEAD>` is the exact commit that was inspected. If the head moved between the
inspection and the merge, the script aborts and the file stays as the record of
a merge that did not happen.

### Reading one

| Field | What it means |
|---|---|
| `codeant_status` | `REVIEWED`, `STALE`, `SKIPPED` or `ABSENT` — exactly one |
| `codeant_reviewed_exact_head` | `true` **only** when a CodeAnt review's `commit_id` equalled `head_sha`. Never true for a skip or an absence. |
| `codeant_findings` | every finding that was live on this head, printed at the time too |
| `findings_handled` | the decision recorded per finding: `FIXED`, `FALSE_POSITIVE` or `ACCEPTED_RISK` |
| `exception_reason` | why CodeAnt did not review this head, when it did not |
| `direct_github_merge_protection` | always `false` — see below |
| `refused` | the refusal message, or `null` if the merge went ahead |

### Why `direct_github_merge_protection` is always false

Because it is false of reality. There is no required GitHub check behind this
and no branch protection rule naming CodeAnt. The evidence file is a record of
what one script checked, not proof that nothing else could have merged.

A merge with no file here is a merge that did not go through the script. That
is possible, it is outside this control, and the missing file is the only trace
it leaves.

### Two fields that look alike and are not

A CodeAnt **review**'s `commit_id` is fixed at the SHA it reviewed. A **line
comment**'s `commit_id` is re-anchored by GitHub onto later commits the comment
still applies to. Only the first answers "was this head reviewed". The full
measurement is in the header of `scripts/merge-pr-with-codeant`.
