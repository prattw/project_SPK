# Securing Project SPK with CAC / YubiKey

**WebAuthn / YubiKey access control is implemented** (see "Implemented" below).
A shared `APP_API_KEY` also remains available as a simpler fallback. This
document lays out the realistic paths to CAC (DoD Common Access Card) and
YubiKey authentication.

## Implemented: WebAuthn (YubiKey) with an admin allowlist

The app gates access with a hardware security key when (and only when) the
WebAuthn environment variables are set — so **local development stays open** and
the **hosted deployment is protected**.

How it works:
- **Enrollment (admin allowlist):** You set a secret `WEBAUTHN_ENROLL_CODE` in
  the hosting environment and share it only with authorized users. On the login
  screen, a user opens "First time? Enroll your key," enters the code, names the
  key, and taps their YubiKey. That key is added to the allowlist.
- **Login:** Thereafter the user clicks "Sign in with security key" and taps the
  key. A signed, http-only session cookie keeps them in for
  `SESSION_MAX_AGE_HOURS` (default 12).
- **Revoking access:** rotate `WEBAUTHN_ENROLL_CODE` to stop new enrollments;
  delete a row from the `auth.db` credentials table (or reset the DB) to revoke
  specific/all keys.

Required environment variables (set these on Railway, leave blank locally):

| Variable | Example | Purpose |
|----------|---------|---------|
| `WEBAUTHN_RP_ID` | `projectspk-production.up.railway.app` | Your domain, no scheme/port |
| `WEBAUTHN_ORIGIN` | `https://projectspk-production.up.railway.app` | Full https origin |
| `WEBAUTHN_ENROLL_CODE` | (a long secret) | Shared with authorized users to enroll |
| `SESSION_SECRET` | (a long random string) | Signs session cookies; keep stable |
| `SESSION_MAX_AGE_HOURS` | `12` | How long a login lasts |
| `AUTH_DB_PATH` | `/data/auth.db` | Put on a **persistent volume** so keys survive deploys |

Notes:
- WebAuthn requires HTTPS — Railway provides this automatically. `RP_ID` must
  match the exact host users visit (no `www.` mismatch).
- If `AUTH_DB_PATH` is not on a persistent volume, enrolled keys are lost on
  redeploy and users must re-enroll.
- This proves possession of an *authorized* key (real access control), not just
  "any YubiKey."

## The short version

| Method | How it authenticates | Works on Railway? | Effort |
|--------|----------------------|-------------------|--------|
| **YubiKey (WebAuthn/FIDO2)** | Browser security-key ceremony | **Yes** — pure app code | Moderate (in-app) |
| **CAC via mTLS** | DoD PKI client certificate at the TLS layer | **No** — Railway cannot terminate client-cert TLS | Requires replatform or proxy |
| **CAC + YubiKey via Login.gov** | OIDC redirect; Login.gov verifies PIV/CAC or security key | Yes (app side is plain OIDC) | Requires an agency agreement with Login.gov |
| **Cloudflare mTLS / Access in front** | Cloudflare validates client certs, forwards to Railway | Yes, with Cloudflare proxying your domain | DoD root CA setup on Cloudflare; cert UX varies |

## Option details

### 1. WebAuthn (YubiKey) — DONE
- FIDO2 registration/login endpoints (`py_webauthn`), a SQLite credential store,
  and signed session cookies are implemented (`app/webauthn_auth.py`).
- Each authorized user registers their YubiKey once (gated by the enrollment
  code); afterwards login is touch-the-key.
- **CAC cannot be used this way** — CACs are smartcards doing X.509/PIV, not FIDO2.

### 2. CAC via mutual TLS
- The server (or a proxy in front) must request and validate a client
  certificate against the **DoD PKI root/intermediate CAs**, then check
  certificate revocation (CRL/OCSP).
- Railway does not support client-certificate TLS termination, so this needs:
  - **Cloudflare** in front (mTLS rules with uploaded DoD CAs), or
  - A replatform: AWS (ALB + mTLS, or NGINX on EC2/GovCloud), Azure
    Application Gateway, or any VPS running NGINX/Caddy with
    `ssl_verify_client`.

### 3. Login.gov (covers both)
- Login.gov supports **PIV/CAC and security keys (YubiKey)** natively.
- The app implements one standard OIDC flow; Login.gov handles the hardware.
- Requires a **partner agency agreement** — viable if this is sponsored by a
  USACE/Army organization, not viable for a personal project.

## Hosting and data caution (read this)

- The GitHub repo is **public**. `DOCUMENTS for RAG/` and `catalog/` are now
  gitignored so corpus files and document inventories never get pushed —
  keep it that way, or make the repo private.
- Railway is **not** FedRAMP/DoD IL-authorized hosting. If any documents are
  CUI/FOUO, hosting them (and their embeddings) on Railway is a compliance
  problem regardless of CAC/YubiKey at the front door. The compliant path is
  AWS GovCloud / Azure Government, which also happens to make mTLS-CAC
  straightforward.
- The OpenAI API also processes the document text; for CUI, the
  government-authorized equivalent is Azure OpenAI Government.

## Recommendation

If users have CACs and the data is government data, the durable answer is a
**government cloud + mTLS (CAC) + WebAuthn (YubiKey fallback)**.
If this stays a pilot on Railway with public/non-sensitive documents,
**WebAuthn (YubiKey) now** is the strongest auth Railway can support, keeping
the API key as a fallback.
