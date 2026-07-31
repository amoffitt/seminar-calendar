# Combined Emory Seminar Calendar

This refactor combines three sources:

- Winship Cancer Institute events
- Department of Human Genetics events
- Department of Biomedical Informatics seminars

Edit `config.yml` to enable sources and change filtering.

## Replace the old layout

Keep your existing `docs/index.html`. Add or replace the files from this package,
then remove the old root-level `scrape_winship.py` after the combined workflow
has succeeded once.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python build_calendar.py
```

## Git workflow

Before editing:

```bash
git pull --rebase
```

After copying the files:

```bash
git add .
git commit -m "Refactor to combined configurable seminar calendar"
git push
```

Run the GitHub workflow manually and inspect the parsed and included counts.
