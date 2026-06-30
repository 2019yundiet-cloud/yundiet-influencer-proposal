# Yundiet Influencer Proposal

Static GitHub Pages deployment for Yundiet proposal pages.

- Sponsorship proposal: `/`
- Mayounni group-buy proposal: `/mayounni-groupbuy/`

## Route Management

- Do not repurpose an existing public path for a new proposal.
- Keep `/` locked to the sponsorship proposal used by influencer DM outreach.
- Add each new partner or campaign proposal under its own directory, such as
  `/partner-campaign/`.
- Register every route in `proposal-link-registry.json` with expected and
  forbidden content markers.
- Validate before committing:

```bash
python3 scripts/validate_proposal_routes.py --local
python3 scripts/test_validate_proposal_routes.py
```

- After pushing, validate the published GitHub Pages routes. GitHub Pages can
  take a short time to propagate, so the validator retries by default.

```bash
python3 scripts/validate_proposal_routes.py --public
```

The repository workflow `.github/workflows/validate-proposal-routes.yml` runs the
same local route check and route-validator tests on direct PRs and pushes so the
root sponsorship page is not accidentally replaced by a partner or group-buy
proposal.
