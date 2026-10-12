# jobs-feed

A weekly collector of open data, research, and analyst postings at education organizations, education and policy research groups, civic tech nonprofits, and NYC government.

Every Monday morning a GitHub Action reads `orgs.csv`, finds each organization's hiring platform, pulls every open posting straight from that platform's public job feed, keeps the plausibly relevant ones, and commits the results to `data/`.

## Sources

- **Organization job boards.** The supported platforms are Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, BambooHR, Recruitee, and Breezy. The collector finds an organization's platform by reading its careers page, including one level of linked pages, and remembers it in `data/platforms.json`.
- **NYC Jobs.** All external city postings come from the [NYC Open Data jobs dataset](https://data.cityofnewyork.us/City-Government/Jobs-NYC-Postings/kpav-sd4t), narrowed to data, research, and analyst titles.
- **Keyword searches across job aggregators.** These catch employers that aren't in `orgs.csv`. Results must mention education or public-interest work in the employer name, title, or opening of the description. The sources are [The Muse](https://www.themuse.com/developers/api/v2) and [Remotive](https://remotive.com/api-documentation), which need no key, and [Adzuna](https://developer.adzuna.com/), which is optional and needs a free key saved as `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`. These postings are marked `Discovered`.
- **USAJOBS (optional).** Federal postings in the NYC area or remote. This source needs a free key from [developer.usajobs.gov](https://developer.usajobs.gov/), saved as the repository secrets `USAJOBS_API_KEY` and `USAJOBS_EMAIL`.

## Output

| File | What's in it |
|---|---|
| `data/jobs.json` | Relevant open postings: title, org, location, pay if listed, posted and first-seen dates, years of experience mentioned, and the first part of the description |
| `data/health.json` | One row per source: which platform it uses, whether it worked, and how many postings it had |
| `data/platforms.json` | Detected hiring platforms, reused on later runs |
| `data/seen.json` | When each posting was first seen, so new postings can be flagged |

The filters only drop postings that are clearly off-target, such as unrelated fields, executive titles, or on-site jobs outside the NYC area. Judging fit is left to whoever reads the output.

## Weekly email (optional)

After each run, the workflow can email a short list of the week's new postings, grouped by organization tier, with pay, location, and closing dates where known. It sends through Gmail and needs:

- **Secrets** (Settings → Secrets and variables → Actions → Secrets): `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`. The app password comes from your Google Account's security settings and requires 2-Step Verification. You can also set `DIGEST_TO` to send to a different address.
- **Variables** (same page, Variables tab; all optional): `MIN_SALARY` drops postings whose listed top of range is below it, `EXCLUDE_TERMS` drops postings mentioning any of a comma-separated list of terms, `EXCLUDE_EMPLOYERS` drops postings from employers whose name contains any of a comma-separated list (prefix a name with `!` to exempt it, as in `Google, !DeepMind`), and `MAX_YEARS` drops postings asking for at least that many years of experience (default 5).

If the secrets aren't set, the email step is skipped and the collector runs as usual.

## Adding or fixing an organization

Add a row to `orgs.csv`. Leave `ats` and `token` blank so the collector detects the platform itself. If detection fails, `data/health.json` shows why. In that case, fill in `ats` and `token` by hand: for example, `greenhouse` and `zearn` for `job-boards.greenhouse.io/zearn`. For Workday, use `workday` and `tenant.wdN.myworkdayjobs.com|tenant|SiteName`.

## Running it locally

```bash
pip install -r requirements.txt
python -m pytest
python -m collector.collect
```
