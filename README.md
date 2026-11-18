# Podcast Curator

A personal podcast feed that fills itself. Every morning, Claude scans your shows, a list of discovery shows, appearances by people you like, and episodes people are talking about. It scores each one against your taste profile and sends about 8 episodes a week into one feed. You subscribe to that feed once in Pocket Casts, and it becomes your whole Up Next queue. Every episode is sent once and never again.

Your feed address, once set up:

```
https://ecto1a2.github.io/podcast-curator/feed.xml
```

## How it works

Each day, around 4 to 5 AM Cleveland time:

1. **Widen the net (weekly).** Claude proposes up to 8 new people, shows, and topics close to what you like (second order, like comedians in Normand's circle) or one step further (third order, like sanctions and shadow fleets next to the drug trade). Each must connect to a listed interest. New names expire after 6 weeks unless they prove themselves.
2. **Gather.** New episodes from your 27 subscriptions, 26 discovery shows, and searches for 19 people and 8 topics. Twice a week, Claude also searches the web for episodes getting buzz.
3. **Score.** Claude rates each new episode 0 to 100 using `config/profile.md`, your notes, and your check-in replies. Topic fit comes first. Among on-topic episodes, guest and host quality decides the ranking.
4. **Send.** About 8 a week:
   - **Regulars, about 3 a week.** Pardon My Take on Monday and Friday, and Stavvy's World's main weekly episode, always go in.
   - **Exploration, 2 of those 5.** Two discovery slots a week are held for episodes found through the new names. They show up as "Exploring:" in Pocket Casts.
   - **Discovery, up to 5 a week, at most 1 a day.** Only episodes scoring 70 or higher. On a slow day, the slot stays empty rather than sending filler.
   - Anything sent is remembered forever and never sent again.
5. **Publish.** The feed and a simple web page at `https://ecto1a2.github.io/podcast-curator/` show the picks, each with a one-line reason.

House rules built in:

- One thoughtful pick (big ideas, systems, or geopolitics) and one short pick (under 30 minutes) whenever available
- No more than 2 picks from one category, and no more than 1 from one show, in any 7 days
- Sam Harris goes in at 75 or higher. Stavvy's bonus previews at 75. Wednesday Pardon My Take only at 92 or higher.
- Parenting, cooking, and narrative history at most once every 2 weeks each
- Two-hour cap (125 minutes, to allow for ads). Your starred backlog is exempt.
- Your 27 unplayed starred episodes drip in, at most 2 a week

Every Sunday, it opens a GitHub issue with the week's picks, the new names it started exploring, and five short questions. Say which new names to keep or drop. GitHub emails you. Reply in the issue, in any format. Claude reads replies daily and uses them for every score after that.

## One-time setup (about 20 minutes)

### 1. Create the repository
1. Go to github.com and sign in as ecto1a2.
2. Click **New** (green button) to make a repository.
3. Name it `podcast-curator`. Choose **Public**. Free GitHub Pages needs a public repository, so anyone who finds it can see your picks and profile. Nothing sensitive is in it.
4. Click **Create repository**.

### 2. Add the files
Easiest: tell Claude the empty repository exists, and it can try to push the files for you.

Or do it yourself:
1. Unzip the file Claude sent you.
2. On the new repository page, click **uploading an existing file**.
3. Drag in everything inside the `podcast-curator` folder, including the `.github` folder. Click **Commit changes**.
4. Check that `.github/workflows/curate.yml` appears in the repository. On Windows, the `.github` folder sometimes gets skipped. If it's missing, click **Add file**, then **Create new file**, type `.github/workflows/curate.yml` as the name, and paste in the contents of that file.

### 3. Get a Claude API key
This is separate from your Claude subscription.
1. Go to console.anthropic.com and sign in.
2. Under **Billing**, add $5 to $10 of credit.
3. Under **Limits**, set a monthly spend limit. $10 is plenty.
4. Under **API Keys**, click **Create Key**. Copy it. You only see it once.

### 4. Give the key to GitHub
1. In your repository, click **Settings**, then **Secrets and variables**, then **Actions**.
2. Click **New repository secret**.
3. Name: `ANTHROPIC_API_KEY`. Secret: paste the key. Click **Add secret**.

### 5. Turn on the web feed
1. In **Settings**, click **Pages**.
2. Under **Source**, choose **Deploy from a branch**.
3. Branch: `main`. Folder: `/docs`. Click **Save**.

### 6. Run it the first time
1. Click the **Actions** tab. If asked, click to enable workflows.
2. Click **Curate podcasts**, then **Run workflow**. Tick **Open a check-in issue now** if you want to see one. Click **Run workflow**.
3. Wait 3 to 5 minutes for a green check mark.
4. Open `https://ecto1a2.github.io/podcast-curator/` to see your first picks.

### 7. Set up Pocket Casts
1. **Clear Up Next.** Your starred episodes stay in your Starred list, and the best of them are already in the starred backlog.
2. **Turn off auto-add** for Pardon My Take and Stavvy's World. The feed sends those now. Keep the subscriptions if you like browsing them.
3. **Subscribe to the feed.** In **Discover**, paste the feed address into search, tap the result, and subscribe.
4. **Turn on auto-add for the feed only.** Set it to add to the top of Up Next, so timely episodes come first.
5. **Set an auto-add limit of about 15**, if your version offers one. That's about two weeks of listening.
6. **Turn on auto-archive for inactive episodes** at 2 to 3 weeks, if offered. Skipped picks then clean themselves up.

Setting names change between app versions. If one is missing, the rest still work.

Optional: make a smart playlist of unplayed episodes from the feed released in the last 14 days. Since each pick is dated the day it was sent, that playlist always shows only current picks.

## Tuning it

Edit these files directly on GitHub. Click the file, then the pencil icon, then **Commit changes**.

| File | What it controls |
|---|---|
| `config/profile.md` | Your taste. Topics, comedians, rules, starred examples. |
| `config/notes.md` | Quick notes like "more short episodes this month." |
| `config/sources.json` | Which shows, people, and topics it searches. |
| `config/seeds.json` | Your starred backlog. Delete a line once heard. |
| `config/settings.json` | Feed size, score cutoffs, swap rules, time windows. |

Changes apply on the next morning's run, or right away if you click **Run workflow**.

## Costs

- GitHub: free.
- Claude API: likely a few dollars a month. Each episode is scored once, then cached for a week. The spend limit from step 3 is your safety net.

## Known limits

- **It can't see what you played.** It sends on a steady weekly budget, not on your listening. Your check-in replies are the only feedback.
- **Sent is final.** It can't pull an episode back out of your queue. That's why the bar is high and the weekly cap is firm. Auto-archive handles cleanup.
- **Its memory lives in the `state` folder.** Don't delete it. Without it, old episodes could be sent again once.
- **Pocket Casts refreshes small feeds slowly.** New picks can take a few hours to appear.
- **Buzz detection is noisy.** It relies on web search and will need tuning.
- **Discovery shows are found by name.** If a name doesn't match, the run notes it and skips that show. Check `state/state.json` under `last_run`, then `errors`.
- **First live test happens on GitHub.** The logic was tested offline with fake feeds. Live Apple Podcasts search, the Claude API, and the web search step run for the first time in your first workflow run.

## Alerts

- **If it breaks,** for example when API credits run out or the key is missing, it opens a GitHub issue titled "Curator needs attention." GitHub emails you. The issue closes itself once a run works again.
- **Every Sunday,** the check-in starts with a health line: episodes sent that week against the target, how many days Claude scoring worked, and any feeds that failed.

## Troubleshooting

- **Red X in Actions.** Click the run, then the failed step, to see the error. Most often the API key is missing or out of credit.
- **"Permission denied" when saving.** Go to **Settings**, **Actions**, **General**, and under **Workflow permissions** choose **Read and write permissions**.
- **Feed address shows 404.** Pages takes a few minutes after step 5. Make sure the first run finished and `docs/feed.xml` exists.
- **Scheduled runs stopped.** GitHub pauses schedules after 60 days with no activity. The daily commits normally prevent this. If it happens, re-enable the workflow in the Actions tab.
