# Security Alerts

## Failed logins

- Scope: `auth.login` failures grouped by source IP or email domain over 10 minutes.
- Triage: review recent `AuditEvent` rows, sample request IDs, source IP, and email domain; confirm whether the failures are concentrated on one actor or many accounts.
- Escalate: page security if the alert is `high`, if multiple accounts are hit from one IP, or if login failures coincide with backend auth validation warnings.

## Privileged actions

- Scope: `rbac.group_member_role_change`, `admin.group_member_remove`, and `admin.group_invite_delete`.
- Triage: verify actor, target resource, route, outcome, and whether the action matches an approved support or product workflow.
- Escalate: treat unexpected successful privileged changes as `high` and preserve request IDs for forensics.

## Banking policy denials

- Scope: backend-enforced `banking.policy_denied` events for MFA or consent policy blocks.
- Triage: inspect `reason_code`, `aal_normalized`, MFA factor count, and source IP; determine whether the pattern is user friction, automation, or attempted control bypass.
- Escalate: investigate bursts against the same actor or source IP, especially when followed by privileged or banking-link events.
