# FCA-Compliant Marketing Text Generator

A Streamlit app that generates UK financial-promotion marketing copy for
Financial Advisers, grounded in specific FCA Handbook / guidance pages, logs
every generation, and flags previously generated posts for review when the
underlying FCA content changes.

**This tool assists drafting only. It is not a substitute for your firm's
compliance sign-off process** — all output should still go through your
usual financial promotion approval workflow (e.g. sign-off by a CF10/CF30 or
equivalent) before publication.

## What it does

- **Generate** — pick Post/Carousel, a category (Retirement, Investment,
  Protection, Mortgage, Employee Benefit, Investment and Estate Planning,
  Pension, Lifestyle), an optional guideline, and a number of posts. The app
  calls Google Gemini with a system prompt built from cached FCA reference text
  and category-specific compliance notes, and returns ready-to-post text.
- **History** — every input and output is logged: timestamp, adviser,
  format, category, guideline, number requested, and each generated post's
  text, keyed to the batch it came from.
- **Compliance Monitor** — tracks these pages (from your source doc):
  - COBS 4 — Communicating with clients, including financial promotions
  - PRIN — Principles for Businesses
  - COBS 2.1 — Acting honestly, fairly and professionally
  - FG15/4 — Social Media and Customer Communications (PDF)
  - FG22/5 — Consumer Duty Guidance (PDF)

  "Run compliance check now" re-fetches each page, hashes its text, and
  compares to the last-known hash. A change is logged in `fca_change_log`.
- **Flagged Posts** — whenever a section's content changes, every
  previously generated post whose generation batch was grounded in the
  *old* version of that section is automatically marked
  `flagged_for_review` and listed here with the adviser to notify. You can
  mark a post "notified" (once you've told the adviser, e.g. by email) and
  "resolved" once it's been reviewed/regenerated.

## How the "did this change affect my post" logic works

Every `generation_batches` row stores a JSON snapshot of
`{section_key: content_hash}` for all tracked FCA sections **at the moment
that batch was generated**. When `check_for_updates()` finds a new hash for
a section, it searches for batches whose snapshot recorded the *old* hash
for that section, and flags every post in those batches. This means only
posts actually generated against the outdated wording are flagged — not
every post ever generated.

## Setup

1. **PostgreSQL** — create a database:
   ```bash
   createdb fca_marketing
   ```
2. **Python deps**:
   ```bash
   python -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```
3. **Configuration** — copy `.env.example` to `.env` and fill in
   `DATABASE_URL` and `GEMINI_API_KEY`.
4. **Run the app** (tables are created automatically on first run):
   ```bash
   streamlit run app.py
   ```
5. Before your first "Generate", go to the **Compliance Monitor** tab and
   click **Run compliance check now** at least once, so the app has cached
   FCA reference text to ground the prompt in.

## Deploying on Streamlit Cloud

1. Push this project to a GitHub repository.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app
  from that repository and set the main file to `app.py`.
3. Add the following values under the app's **Settings > Secrets**. Use a
  hosted PostgreSQL connection string; a local PostgreSQL or Ollama server
  is not reachable from Streamlit Cloud.

  ```toml
  DATABASE_URL = "postgresql://user:password@host:5432/database"
  AI_PROVIDER = "gemini"
  GEMINI_API_KEY = "your-gemini-api-key"
  GEMINI_MODEL = "gemini-2.5-flash"
  FCA_CHECK_INTERVAL_HOURS = "24"
  ```

  The app reads these values from Streamlit secrets. Never commit `.env` or
  `.streamlit/secrets.toml`; both are excluded by `.gitignore`.

## Keeping the FCA monitor current automatically

`monitor_cli.py` runs the same check as the "Run compliance check now"
button, standalone, so you can schedule it (cron, systemd timer, Task
Scheduler, a GitHub Action, etc.):

```
0 6 * * * cd /path/to/fca_marketing_app && /usr/bin/python3 monitor_cli.py >> /var/log/fca_monitor.log 2>&1
```

To wire up actual email/Slack notifications when posts are flagged, extend
`monitor_cli.py` (or a copy of it) to read the `notified = FALSE` rows in
`post_flags` after `check_for_updates()` runs and send your notification of
choice — the schema already tracks adviser name/email against every post via
`generation_batches.advisor_id`.

## Notes / limitations

- The monitor does simple whole-page text hashing, not clause-level diffing.
  A change anywhere on a tracked page (including minor site copy) will
  trigger a flag; review the change log entry and the adviser's flagged
  posts to judge materiality before asking them to regenerate.
- PDF text extraction (`FG15/4`, `FG22/5`) is done with `pypdf`, which can
  occasionally miss text in complex PDF layouts — spot-check
  `fca_sections.content_text` if a check seems to be missing an update you
  know happened.
- The FCA Handbook and guidance library is much larger than the five pages
  tracked here (which reflect the "financial promotions" scope in your
  source document). Add more pages by adding entries to `FCA_SECTIONS` in
  `fca_monitor.py`.
- Gemini model names change over time — if `GEMINI_MODEL` in `.env`
  stops working, check the current Gemini model list in Google AI Studio.

## File layout

```
app.py            Streamlit UI (Generate / History / Compliance Monitor / Flagged Posts)
generator.py      Builds the compliance-grounded prompt and calls the Gemini API
fca_monitor.py    Fetches/hashes tracked FCA pages, detects changes, flags posts
db.py             Thin PostgreSQL access layer
schema.sql        Table definitions
monitor_cli.py    Standalone entrypoint for scheduled compliance checks
config.py         Environment-variable configuration
requirements.txt
.env.example
```
