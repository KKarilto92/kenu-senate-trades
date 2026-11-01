# kenu-senate-trades

US Senate periodic transaction reports from 1 January 2025, read from the public eFD site (efdsearch.senate.gov) by `collect.py`
on GitHub Actions, every 4 hours and on demand. The site refuses visitors from outside the US, which is why this runs here.

- `data/index.json`: every report the Senate's search lists
- `data/reports/`: one file per report: electronic reports as data and as the saved page, paper reports' details
- `data/paper/`: paper reports' page images
- `last_run.json`: what the last run did

This repository holds only public Senate records and the collector. It holds no credentials.

The site's notice sets out prohibitions on obtaining and using financial disclosure reports. Read it before using these records.
