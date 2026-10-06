# ADR 0004: Browser-approved development-agent access

## Status

Accepted on 2026-10-06.

## Context

TaskHub originally authenticated only browser users with signed, revocable session cookies and
CSRF tokens. A healthy Seed therefore remained unusable to Codex or another development agent
unless the operator disclosed an administrator password or copied a browser cookie. Both options
break credential isolation, revocation, auditability, and cross-platform automation.

## Decision

TaskHub provides a device-style pairing flow for development agents:

1. The agent starts a ten-minute pairing and receives a non-secret confirmation code plus a
   high-entropy exchange secret.
2. An authenticated browser user reviews the label and confirmation code, then grants a bounded
   RBAC role and expiry of 1–90 days.
3. The agent exchanges its secret once and writes the returned Bearer credential to a private local
   file. The ordinary connection document stores only that file path.
4. TaskHub stores only a SHA-256 digest of an active credential. The short-lived unclaimed grant is
   encrypted with the deployment's existing Fernet master key and is deleted after exchange.
5. Bearer requests use the same permission resolver and operational audit as browser requests. They
   do not require CSRF because they do not use ambient browser cookies. An explicit invalid or
   revoked Bearer credential fails with 401 and never falls back to a browser session.

The default role is `project_owner`, sufficient to connect projects and create or resume delivery
runs without granting platform-security administration. Credentials default to 30 days and can be
revoked immediately from the access-security console.

## Alternatives considered

- Reusing the administrator password was rejected because it cannot be narrowly scoped, safely
  stored in a connection document, independently revoked, or attributed to one agent.
- Copying a session cookie was rejected because it bypasses the HttpOnly browser boundary and
  couples automation to idle timeout and CSRF implementation details.
- A permanently configured shared API key was rejected because it recreates the deployment-wide
  shared-secret problem and lacks per-agent ownership and revocation.

## Consequences

- The Seed configuration volume gains `agent-access.json`; backups already cover that volume and
  its encryption key.
- Operators must complete one browser approval for every new agent or replacement credential.
- API clients must load the token from the protected file and send `Authorization: Bearer ...`.
- If the encryption key is absent, pairing fails closed and password/session login remains intact.
