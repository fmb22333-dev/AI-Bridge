# Security Policy

## Reporting

Please do not publish credentials, access tokens, private keys, local Bridge state, private Bus state, or machine-specific project data in issues or pull requests.

For security-sensitive reports, contact the repository owner through a private channel before opening a public issue.

## Repository hygiene

The product repository must not contain:

- GitHub or service access tokens;
- DPAPI-encrypted credential blobs;
- private keys or certificate bundles;
- per-install `.ai-bridge` / `.ai_bridge` state;
- user Bus command/result/status history;
- machine-specific project paths or Bridge IDs.

Tests may use clearly synthetic identifiers and example paths.
