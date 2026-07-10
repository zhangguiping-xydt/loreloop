# Zero-context usability study

This protocol is ready to run, but the repository intentionally contains no
fabricated participant outcomes. A coding agent, maintainer, or deterministic
smoke test is not a substitute for a person who has never seen the design.

## Participant and setup

- Recruit someone who has not read this repository, its design documents, or a
  product explanation. Use a pseudonymous id; collect no names, emails, screen
  contents, credentials, or environment variables in the repository.
- Give them a clean machine or disposable account with Git, Python 3.11+, one
  supported coding-agent CLI, and the README only.
- Ask them to install knowhelm and complete the bundled legacy-upload task.
  The observer may say only: "work from the README; think aloud if comfortable."
- Start timing when the participant opens the README. First success is the
  first `Verdict: ACCEPTED` report followed by a successful harvest.

## Observe without coaching

Record each wrong turn, help lookup, recovery attempt, abandoned step, and time
to first success. At the end, ask the participant to explain in their own words:

1. which knowledge is treated as established versus a reference;
2. who is allowed to approve, verify, and supersede knowledge;
3. why an agent editing SQLite cannot grant itself trusted status;
4. what must happen before a run can feed knowledge back.

Copy `eval/usability/session-template.json` to
`eval/usability/sessions/<pseudonym>-round-N.json` and fill observations only.
Validate and aggregate records with:

```bash
python eval/usability.py
```

## Iteration rule

After at least three participants in a round, rank friction by frequency, then
by whether it blocked completion. Fix the highest-ranked product, documentation,
or automated-check issue. Keep the failing records, increment `study_round`, and
rerun with new zero-context participants. A recurring mistake must become a CLI
guardrail, clearer help, a doctor check, or a documentation fix—not tribal
knowledge for the observer.

Published reports must state participant count and preserve failed/abandoned
sessions. Do not report a completion rate when there are no real sessions.
