# jobs-feed

A daily collector of open data, research, and analyst postings at education organizations, education and policy research groups, universities, civic tech nonprofits, and NYC government. It keeps jobs in the NYC area (including Nassau County), the DC metro, and remote jobs anywhere in the US.

Every night a GitHub Action reads `orgs.csv`, finds each organization's hiring platform, pulls every open posting straight from that platform's public job feed, keeps the plausibly relevant ones, and commits the results to `data/`.

## Sources

- **Organization job boards.** The supported platforms are Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, BambooHR, Recruitee, Breezy, ADP Workforce Now, Oracle Recruiting Cloud, Paylocity, Pinpoint, JazzHR, iCIMS and Jibe, Jobvite, Rippling, UKG Pro, UKG Ready, Cornerstone, Eightfold, Trakstar Hire, PageUp, and careers pages that link straight to individual postings. A board found by guessing that turns out to be empty is reported as `empty` and re-detected the next week. `python -m collector.probe` (or the "Probe failing sources" workflow) diagnoses sources that fail. The collector finds an organization's platform by reading its careers page, including one level of linked pages, and remembers it in `data/platforms.json`. When a source fails on a run (many Workday sites answer HTTP 429 to GitHub's servers on some runs and not others), its postings from the previous run stay in `jobs.json` for up to 7 days after they were last seen, marked `carried_over`, so one blocked run doesn't drop them.
- **NYC Jobs.** All external city postings come from the [NYC Open Data jobs dataset](https://data.cityofnewyork.us/City-Government/Jobs-NYC-Postings/kpav-sd4t), narrowed to data, research, and analyst titles.
- **Keyword searches across job aggregators.** These catch employers that aren't in `orgs.csv`. Results must mention education or public-interest work in the employer name, title, or opening of the description. The sources are [The Muse](https://www.themuse.com/developers/api/v2) and [Remotive](https://remotive.com/api-documentation), which need no key, and [Adzuna](https://developer.adzuna.com/), which is optional and needs a free key saved as `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. Two education job boards are also read: [Edtech.com](https://www.edtech.com/jobs) (its sitemap of the past week's postings, then each relevant posting's structured data) and [HigherEdJobs](https://www.higheredjobs.com/) (remote postings only). These postings are marked `Discovered`, unless the employer matches an organization in `orgs.csv`; then they take that organization's tier and category, so a tier 1 organization whose own board can't be read still ranks as tier 1.
- **New York boards.** [StateJobsNY](https://statejobs.ny.gov/public/vacancyTable.cfm) (every public state agency vacancy; NYC and Nassau counties kept), [cuny.jobs](https://cuny.jobs/) (its RSS feed), [NYN Jobs](https://jobs.nynmedia.com/) and [City & State New York Jobs](https://jobs.cityandstateny.com/) (their RSS feeds, which list only the newest postings, so daily runs matter), and the [NYS Attorney General's postings](https://ag.ny.gov/job-postings) (title, bureau, and deadline; the postings themselves are PDFs). NYC Health + Hospitals uses a PeopleSoft candidate site that can't be read this way.
- **USAJOBS (optional).** Federal postings in the NYC area, the DC metro, or remote. This source needs a free key from [developer.usajobs.gov](https://developer.usajobs.gov/), saved as the repository secrets `USAJOBS_API_KEY` and `USAJOBS_EMAIL`.

## Output

| File | What's in it |
|---|---|
| `data/jobs.json` | Relevant open postings: title, org, location, pay if listed, posted and first-seen dates, years of experience and advanced degrees required, and the description |
| `data/health.json` | One row per source: which platform it uses, whether it worked, and how many postings it had |
| `data/platforms.json` | Detected hiring platforms, reused on later runs |
| `data/seen.json` | When each posting was first seen (date, and time for postings found since daily runs began), so new postings can be flagged |
| `data/daily_candidates.json` | The past seven days' new postings that pass the clear-cut hard filters, each with `first_seen_at`, flags for what still needs a human read, and a trimmed `requirements_and_duties` text, plus a log of what was dropped and why |
| `data/email_status.json` | Counts from the last email step and the date an email last went out |
| `data/analysis.md` | What the postings ask for: tools, methods, experience, and pay, overall and by role family |

The filters only drop postings that are clearly off-target, such as unrelated fields, executive titles, or on-site jobs outside the NYC and DC areas. Postings listed in another city are kept when the description says the job can be done fully remotely ("can be performed remotely", "fully remote", "remote within the US"); hybrid schedules elsewhere don't count. Judging fit is left to whoever reads the output.

## Schedule and the daily screen

The workflow runs at 22:17 UTC Sunday to Friday and again at 05:17 UTC Monday to Saturday (GitHub schedules in UTC only, and has started scheduled runs here 7 to 14 hours late, so the main run starts the evening before a morning reader needs it). `workflow_dispatch` runs it by hand.

After collecting, `python -m collector.screen` writes `data/daily_candidates.json`. It drops only clear cases: listed pay that tops out under $60,000, an advanced degree required with no experience route, 5+ years required, policing, corrections, intelligence, and military employers, defense contractors, frontier AI and big-tech AI employers (except Anthropic and Google DeepMind), charter and private schools, finance and sales titles, in-person worksites in the Bronx, Staten Island, Jersey City, or Hoboken, and fully on-site DC roles. Anything less clear passes with a flag. A reader that remembers its last run takes the candidates with `first_seen_at` after it; the seven-day window means a missed run or check is caught up rather than lost.

Math teaching postings at Queensborough Community College (adjunct and lecturer titles from cuny.jobs) are a separate lane: they skip the title filter and the screen, and land in `teaching_leads` in `daily_candidates.json`.

The weekly email (`collector/email_digest.py`) is no longer sent by the workflow; it can still be run by hand.

## Adding or fixing an organization

Add a row to `orgs.csv`. Leave `ats` and `token` blank so the collector detects the platform itself. If detection fails, `data/health.json` shows why. In that case, fill in `ats` and `token` by hand: for example, `greenhouse` and `zearn` for `job-boards.greenhouse.io/zearn`. For Workday, use `workday` and `tenant.wdN.myworkdayjobs.com|tenant|SiteName`.

## Running it locally

```bash
pip install -r requirements.txt
python -m pytest
python -m collector.collect
```
