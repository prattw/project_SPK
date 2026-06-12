# Securing Project SPK with CAC / YubiKey

The app currently uses a shared `APP_API_KEY`. This document lays out the
realistic paths to CAC (DoD Common Access Card) and YubiKey authentication.

## The short version

| Method | How it authenticates | Works on Railway? | Effort |
|--------|----------------------|-------------------|--------|
| **YubiKey (WebAuthn/FIDO2)** | Browser security-key ceremony | **Yes** — pure app code | Moderate (in-app) |
| **CAC via mTLS** | DoD PKI client certificate at the TLS layer | **No** — Railway cannot terminate client-cert TLS | Requires replatform or proxy |
| **CAC + YubiKey via Login.gov** | OIDC redirect; Login.gov verifies PIV/CAC or security key | Yes (app side is plain OIDC) | Requires an agency agreement with Login.gov |
| **Cloudflare mTLS / Access in front** | Cloudflare validates client certs, forwards to Railway | Yes, with Cloudflare proxying your domain | DoD root CA setup on Cloudflare; cert UX varies |

## Option details

### 1. WebAuthn (YubiKey) — implementable now
- Add FIDO2 registration/login endpoints (`py_webauthn` library), a small user
  store, and session cookies.
- Each authorized user registers their YubiKey once; afterwards login is
  touch-the-key.
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
