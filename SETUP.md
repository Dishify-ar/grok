# Dishify — Firebase Auth setup

This covers what changed under the hood (`admin.html`, `owner.html`, and the new
`firebase-rules.json`) and the manual steps only you can do from the Firebase console —
none of this can be done from the HTML files themselves.

**I could not test any of this against your real Firebase project** — this sandbox has no
network access to it. Everything below was verified against a local mock that reproduces
Firebase's documented Auth + Realtime Database behavior (see the "What was tested" note at
the end), but please run through the checklist below on a staging project, or at a quiet
time, before relying on it in production.

## What changed

- `admin.html`'s hardcoded password (`dishify2024`, visible to anyone who viewed source) is
  gone. Admin access now requires a real Firebase Auth account **and** a matching entry
  under `/admins/{uid}` in the database.
- `owner.html`'s plaintext, database-stored password is gone. Each restaurant now has its
  own Firebase Auth account (owner signs in with email + password), linked to their
  restaurant via `/owners/{uid}/rid`.
- Adding a restaurant in admin.html now also creates that restaurant's owner account and a
  lean `/public_restaurants/{rid}` mirror (name, cuisine, description, photo, vibe, demo
  flag only — no owner email, no menu) that the landing page reads instead of the full
  `/restaurants` tree.
- `firebase-rules.json` is a full replacement for whatever rules your project has now —
  it's written to match this new code exactly, not to layer on top of the old rules.

## 1. Enable Email/Password sign-in

Firebase console → **Authentication** → **Sign-in method** → enable **Email/Password**.
(Nothing works until this is on — both admin and owner login use it.)

## 2. Deploy the rules

Firebase console → **Realtime Database** → **Rules** → paste in `firebase-rules.json` →
**Publish**. Skim the "What each part enforces" section below first so a rule that looks
too strict or too loose for your setup doesn't surprise you after publishing.

## 3. Create the first admin account

The client can never grant admin access to itself — `/admins/{uid}` is `.write: false` for
everyone on purpose, so a compromised or buggy client can't self-promote. That means step
one has to happen by hand:

1. Firebase console → **Authentication** → **Users** → **Add user**. Enter the email and
   password you want to use as an admin.
2. Copy that user's **UID** from the users list.
3. Firebase console → **Realtime Database** → **Data** → at the root, add a child node
   `admins`, then under it a child named exactly that UID, with the value `true` (boolean,
   not the string `"true"`).
4. Open `admin.html`, sign in with that email/password. You should land on the dashboard.

To add more admins later, repeat: create the Auth user, then add their UID under `/admins`
with value `true`. There's no in-app "make this person an admin" button by design.

## 4. Owner accounts

These are created automatically now — when an admin fills in "Add Restaurant" with an
owner email and temporary password, admin.html creates that Firebase Auth account and the
`/owners/{uid}/rid` link for you. Nothing to do here except pass the owner their email and
temporary password (shown in the success toast after adding — write it down, it isn't shown
again). The `demo` checkbox on that form controls whether the restaurant's landing-page card
is clickable — check it for the one venue you want the public to actually click into.

## What each part of the rules enforces

- **`/admins/{uid}`** — a user can read *only their own* entry (so admin.html can check "am
  I an admin"), and nobody can write it from a client, ever. Bootstrapped by hand (step 3)
  and grown the same way.
- **`/owners/{uid}`** — a user can read their own `rid`; only an existing admin can write it
  (this is how admin.html links a new owner account to their restaurant).
- **`/restaurants/{rid}`** — publicly readable (menu.html, ar.html, and the login screens
  all need this with no auth), writable only by admins.
- **`/public_restaurants/{rid}`** — same shape, publicly readable, admin-only write. This is
  what the landing page actually subscribes to.
- **`/orders/{rid}`** — *listing* a restaurant's orders (reading or writing the whole node)
  requires being that restaurant's owner or an admin. Reading or writing one *specific*
  `$orderId` is left open, since a diner placing or checking on their own order has no
  account to authenticate with — the push ID itself is the only thing standing in for
  a credential.
- **`/inquiries`** — anyone can submit one (the landing page's contact form), only admins
  can read the list.

## Known gaps — please read before you rely on this

- **`ownerEmail` and `ownerUid` are still on the publicly-readable `/restaurants/{rid}`
  node.** Nothing in the UI displays them to a diner, but anyone who queries that path
  directly (e.g. via the REST API) can read them. This is a real but much smaller exposure
  than the plaintext password it replaces. The clean fix is splitting those two fields into
  a `/restaurants_private/{rid}` node with rules like the ones above, readable only by
  admins and that restaurant's own owner — I didn't do this here because owner.html and
  admin.html would both need to read from two locations instead of one, and I'd rather flag
  it than rush it untested.
- **`/orders/{rid}/{orderId}` is fully open (read and write, no auth) by necessity** — this
  app has no diner accounts, so there's nothing else to check against. I only confirmed the
  "diner pushes a new order" and "owner reads/updates status" flows; I did not fully audit
  every write path (order cancellation, in-order chat, etc.) against these rules. **Test
  the full order lifecycle against these rules before trusting them in production**, and
  strongly consider turning on **Firebase App Check** (free, no code changes to the rules
  themselves) — it won't add real authentication, but it will filter out most non-browser
  traffic hitting that open endpoint.
- **Deleting a restaurant in admin.html does not delete the owner's Firebase Auth
  account.** It removes `/restaurants/{rid}`, `/orders/{rid}`, and the public mirror, but
  the now-orphaned login still exists. Remove it by hand from **Authentication → Users** if
  you want it gone.
- **No self-service "forgot password" for owners.** A locked-out owner currently needs an
  admin to reset their password from the Firebase console (Authentication → Users → ⋮ →
  Reset password). Adding `auth.sendPasswordResetEmail(email)` to owner.html's login screen
  would close this gap in a few lines, if you want it.
- **Editing an existing restaurant's owner email isn't supported from admin.html** — that
  field is shown read-only in the edit modal. Changing an owner's sign-in email needs the
  Firebase Admin SDK (a server-side call), which is outside what a static HTML page can do.

## What was tested

Everything above the "Known gaps" section — admin login (right/wrong password, signed-in
non-admin gets rejected), the full "admin creates a restaurant → owner logs in from a
completely fresh browser tab" chain, wrong-password rejection, an auth account with no
`/owners` mapping being rejected, and the password-change flow (reauthenticate, update,
log in again with the new password) — was run against a from-scratch JavaScript mock of
Firebase Auth + Realtime Database, backed by a small local server so state genuinely
persisted across separate page loads the way a real backend would. That confirms the
*application code* behaves as intended. It does **not** confirm the rules file is bug-free
against Firebase's actual rules engine — please use the Rules Playground (Realtime Database
→ Rules → the "Simulator" tab) to run a few reads/writes through it yourself before this
goes live.
