# Sign-in and session overhaul - Brief

Task file: [auth-flow.tmda](./auth-flow.tmda) - open it to see tracked progress.

> This document is the *why*. `auth-flow.tmda` is the *what*, one checkbox per
> piece of work. Agents should read this file first, then load the task file
> to see where things actually stand.

## What we want to do

Replace the current email + password sign-in with an OAuth flow backed by a
proper session store, so that we stop keeping password hashes in our own
database and can revoke a session the moment it looks suspicious.

The work breaks down into: picking a provider, deciding how sessions are
stored and rotated, wiring the callback, and putting a rate limit in front of
the token endpoint so the login page stops being our cheapest attack surface.

## Why it matters

Support is currently spending about four tickets a day on "I can't log in",
almost all of which trace back to the session cookie expiring mid-form with no
recovery path. Moving to OAuth with rotating sessions removes that class of
ticket outright and gets us to a posture the security review has already said
we will need before the enterprise pilot.

## Design notes

### OAuth provider

Provider must support OIDC, must not require a paid tier for the volume we
expect (about 40k logins/day), and must be usable from a server-side callback
with no client-side secret handling.

### Session storage

Sessions live server-side. The cookie holds an opaque id only. Refresh every
15 minutes of activity, absolute lifetime 12 hours, rotate the id on every
privilege change. Losing the session store must degrade to a logged-out state,
never to a crash.

### Rate limiting

The token endpoint gets its own budget, separate from the general API budget.
Budget: 10 requests/minute per IP and 5 failed attempts per account per
5 minutes before a soft lock. Anything over the budget returns `429` with a
`Retry-After` header - never a bare `401`, because clients retry those.

## Constraints and non-goals

- Password reset is tracked separately in `security-hardening.tmda`.
- No user-visible branding changes in this pass.
- We are not supporting social providers beyond the one chosen provider.

## What "done" looks like

- A new user can go from landing page to authenticated in under 30 seconds.
- An operator can revoke any session from the admin console within 5 seconds.
- The token endpoint returns `429` with `Retry-After` under a synthetic
  100 req/s burst.
- The security review signs off on the session storage decision.
