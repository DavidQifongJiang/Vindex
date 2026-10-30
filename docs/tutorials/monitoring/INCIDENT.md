# Vindex local incident worksheet

Copy this worksheet for each practice incident. Use UTC timestamps.

- Scenario and date:
- Incident owner:
- User impact (observed, rather than assumed):
- Severity and why:
- First failed probe:
- Alert became pending:
- Alert became firing:
- First notification received:
- First successful probe:
- Resolved notification received:
- Detection delay: first notification minus first failed probe.
- Recovery time: first successful probe minus first failed probe.
- Evidence: dashboard time range, probe details, relevant logs, drill JSONL.
- Hypothesis and what confirmed/refuted it:
- Mitigation taken:
- Recovery checks:
- Root cause / contributing factors:
- One follow-up, owner, and acceptance test:

## Questions to answer after drill 1

1. Why did probe_success fall to 0 while up stayed at 1?
2. Why was notification slower than the first failed request?
3. Why does a 200 response from /health not establish successful search?
4. Would monitoring on this laptop report the laptop losing power?
5. Which changes would make this setup appropriate for actual on-call use?

