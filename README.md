# SEO & Auditor Tracker

This repository contains our automated daily site link auditor (`auditor.py`) and the unified tracking script for GSC, GA4, PageSpeed, and OpenPageRank (`seo_tracker.py`).

## Security Note
All API keys, Spreadsheet IDs, and Google Auth JSON files have been removed from the source code and are stored securely in **GitHub Actions Secrets**. **Never commit `credentials.json` or `token.json` to this repository.**

## Automation Setup
To enable cloud automation, create a file at `.github/workflows/daily_run.yml` and paste the following configuration. This securely injects the secrets at runtime and triggers both scripts sequentially every morning at 5:00 AM UTC.

```yaml
name: Daily SEO & Auditor Tracker
on:
  schedule:
    - cron: '0 5 * * *'
  workflow_dispatch:

jobs:
  run-tracker:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - uses: actions/setup-python@v4
      with:
        python-version: '3.10'

    - name: Install Dependencies
      run: pip install -r requirements.txt

    - name: Create Auth Files from Secrets
      run: |
        echo '${{ secrets.GCP_CREDENTIALS }}' > credentials.json
        echo '${{ secrets.GCP_TOKEN }}' > token.json

    - name: Run Link Auditor Script
      env:
        SHEET_ID: ${{ secrets.SHEET_ID }}
      run: python auditor.py

    - name: Run SEO Tracker Script
      env:
        SHEET_ID: ${{ secrets.SHEET_ID }}
        PAGESPEED_API_KEY: ${{ secrets.PAGESPEED_API_KEY }}
        OPENPAGERANK_API_KEY: ${{ secrets.OPENPAGERANK_API_KEY }}
      run: python seo_tracker.py
```
