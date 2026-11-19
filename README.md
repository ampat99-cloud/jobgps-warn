# JobGPS WARN feed

Every day a GitHub Action downloads the public WARN layoff notices from every US state that publishes them
(biglocalnews/warn-scraper, Apache-2.0), cleans them into one shape (biglocalnews/warn-transformer, Apache-2.0)
and writes the last 400 days here:

- `data/coverage.json`: which state lists were read, when, and how many notices each had.
- `data/warn/<letter>.json`: notices grouped by company name (first letter of the simplified name).

The JobGPS Chrome extension reads these files to answer "No WARN Act notice filed (12 months)".
WARN notices are public employer filings; no personal data is stored. No secrets are needed.
