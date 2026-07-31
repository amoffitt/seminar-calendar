# Winship Seminar Calendar

This repository scrapes the Winship events website and generates an Outlook-compatible
calendar at `docs/seminars.ics`.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python scrape_winship.py
```

## Run on GitHub

Open the repository's **Actions** tab, choose **Build seminar calendar**, and click
**Run workflow**.

The workflow also runs daily at 10:00 UTC (6:00 AM Eastern during daylight-saving time).

## Publish with GitHub Pages

1. Open **Settings → Pages**.
2. Under **Build and deployment**, select **Deploy from a branch**.
3. Select the `main` branch and `/docs` folder.
4. Save.

The calendar URL will normally be:

`https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPOSITORY-NAME/seminars.ics`

Subscribe to that URL from Outlook rather than importing it once.
