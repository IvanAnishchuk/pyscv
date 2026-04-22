# Security Policy

## Supported Versions

Only the latest release receives security fixes.

| Version | Supported |
|---------|-----------|
| latest  | yes       |
| older   | no        |

## Reporting a Vulnerability

**Please do not file public GitHub issues for security vulnerabilities.**

Report privately via one of the following channels:

1. **GitHub Private Vulnerability Reporting**
   Use the "Report a vulnerability" button on the
   [Security tab](https://github.com/IvanAnishchuk/pyscv/security/advisories/new).

2. **Email**
   Send to `ivan@ivananishchuk.net`.

Please include:

- A description of the issue and its impact
- Steps to reproduce (proof-of-concept if possible)
- Affected versions
- Your name and affiliation (optional, for credit)

## Response SLA

- **Triage**: within 7 days of report
- **Fix + advisory**: within 90 days for high/critical issues

## Verifying Releases

All releases are:

- Built by GitHub Actions from a tagged commit
- Published to PyPI via **trusted publishing (OIDC)**
- **Signed with sigstore** (keyless signing)
- Accompanied by a **CycloneDX SBOM**

```sh
# Verify sigstore signature
uv tool run sigstore verify identity \
    --cert-identity-regexp '^https://github\.com/IvanAnishchuk/pyscv/\.github/workflows/release\.yml@' \
    --cert-oidc-issuer 'https://token.actions.githubusercontent.com' \
    --bundle pyscv-*.whl.sigstore \
    pyscv-*.whl

# Verify GitHub attestation
gh attestation verify pyscv-*.whl --owner IvanAnishchuk
```

## Safe Harbor

We support responsible security research. Good-faith efforts to discover
and report vulnerabilities will not be met with legal action.
