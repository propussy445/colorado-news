# Colorado This Week

A website showing the biggest Colorado news stories from the past 7 days. It updates itself every morning.

## How it works

- `build_news.py` reads RSS feeds from Colorado outlets (Colorado Sun, Denver Post, Colorado Newsline, Denverite, FOX31, KRDO, Sentinel Colorado) and a Google News search for "Colorado". It keeps the past week's stories, groups headlines about the same event, ranks them by how many outlets covered them, and writes `news.json`.
- `index.html` reads `news.json` and shows the stories, with search and outlet filters.
- `.github/workflows/update-news.yml` runs the script every day on GitHub's servers and saves the new `news.json`.

You don't need a server or an API key, and you don't need to install anything.

## Setup (one time)

1. Create a new GitHub repo and upload all these files, including the `.github` folder.
2. In the repo, go to **Settings → Pages**. Under "Build and deployment," set Source to **Deploy from a branch**, pick **main** and **/ (root)**, and click **Save**.
3. Go to the **Actions** tab. If GitHub asks, enable workflows. Click **Update Colorado news**, then **Run workflow**. After about 30 seconds, `news.json` will appear in the repo.
4. Your site will be live at `https://YOUR-USERNAME.github.io/REPO-NAME/` within a minute or two.

After that, it updates every morning by itself.

## Tweaking

Edit the settings at the top of `build_news.py`:

- `FEEDS`: add or remove outlets. A feed that breaks gets skipped.
- `MAX_STORIES`: how many stories appear on the page.
- `SIMILARITY`: raise it if unrelated stories get lumped together, lower it if the same story shows up twice.

To change the update time, edit the `cron` line in the workflow file. Times there are in UTC.

## Run it on your own computer

```
python3 build_news.py
python3 -m http.server
```

Then open http://localhost:8000.
