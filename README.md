# Masters Team Scoreboard

A deploy-ready static landing page that compares two Masters teams and updates itself on a schedule.

## What is in this repo

- `site/` - the public landing page
- `site/data/latest.json` - the generated scoreboard data file
- `scripts/update_scores.py` - fetches the leaderboard page and turns it into JSON
- `teams.json` - the two team rosters and player-name aliases
- `.github/workflows/deploy-scoreboard.yml` - scheduled GitHub Pages deployment workflow

## Fastest setup

1. Create a new GitHub repository.
2. Upload everything in this folder.
3. Go to **Settings -> Pages**.
4. Under **Build and deployment**, choose **GitHub Actions** as the source.
5. Push to `main`.
6. Run the **Deploy Masters team scoreboard** workflow once from the **Actions** tab.
7. Your URL will be:
   - `https://<your-user>.github.io/<repo-name>/`

## What to customize

### Team names

Edit `teams.json`:

- change `displayName`
- change `subtitle`
- add or edit aliases if a player's name is commonly misspelled

### Scoring rule

Right now the site totals all 10 players' scores to par.

If your pool uses a different rule, update:

- `event.scoringRule` in `teams.json`
- the total calculation in `scripts/update_scores.py`

## Data source notes

This version is built for quick setup and uses the ESPN Masters leaderboard page URL in `teams.json`.

If you want a fully licensed production version, keep the landing page and swap out only the fetch logic in `scripts/update_scores.py` for a paid golf data API.

## Local test

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/update_scores.py --input-file sample/espn-field-sample.html --out site/data/latest.json
```

Then open `site/index.html` in a browser.

## Notes

- The workflow is scheduled every 10 minutes.
- The landing page also polls `latest.json` every 60 seconds while open.
- Players who have not started are counted as even par (`E`).
