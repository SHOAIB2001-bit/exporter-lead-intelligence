# Exporter Lead Intelligence v2

Maharashtra-first lead research and calling workflow for Alibaba.com seller subscriptions.

## Included
- Streamlit dashboard
- CSV import/export
- Morning calling list
- Call outcomes and follow-up dates
- Weighted evidence-based score
- Free Lead Finder with targeted Google search links, industry/city filters, public directory links, and CSV import
- Optional public-web candidate discovery through Brave Search API
- Supabase database support for online persistence
- Local SQLite fallback for local testing only

## Important
Search results are unverified candidates. The app does not guarantee that a company is an exporter, has expansion intent, has a valid phone number, or is not already an Alibaba subscriber. Verify the source page and contact before calling. It does not scrape restricted websites or bypass access controls.

## A. Run locally
Requires Python 3.10+.

```bash
python -m venv .venv
```

Windows PowerShell:
```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Local mode uses `leads.db` in this folder.

## B. Set up online database
1. Create a project at https://supabase.com/.
2. Open SQL Editor and run the contents of `supabase_schema.sql`.
3. Copy the project URL and an appropriate server-side API key from Supabase project settings.
4. In Streamlit Community Cloud app settings, add secrets:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY = "YOUR_SERVER_SIDE_KEY"
```

Never put keys in `app.py`, commit them to Git, or share them in chat. Keep the app private while using a privileged server-side key. For a public app, implement user authentication and row-level security rather than exposing broad database access.

## C. Free lead research (no search API key)
1. Open **Free Lead Finder** in the app.
2. Select an industry and city / industrial area.
3. Open the generated search links and verify each company on its own website or a credible directory.
4. Download the CSV template, add researched company records, and import them.
5. Keep source URLs and do not mark a company as an exporter unless you find evidence.

This mode is free but involves reviewing search results manually. It does not scrape Google or automatically create verified leads in the background.

## D. Enable optional API-based candidate discovery
1. Create a Brave Search API account and obtain an API key, after reviewing pricing and usage terms.
2. Add this secret in Streamlit app settings:

```toml
BRAVE_SEARCH_API_KEY = "YOUR_KEY"
```

3. Open **Discover Leads**, select industries and run a search.
4. Review candidate pages, confirm company identity and Maharashtra location, then save candidates. They remain marked `Needs research` and start with zero score.

Search API pricing, quotas, coverage and terms may change. This integration is optional.

## E. Deploy to Streamlit Community Cloud
1. Create a private GitHub repository and upload `app.py`, `requirements.txt`, and `supabase_schema.sql`.
2. Go to https://share.streamlit.io/ and deploy the repository with `app.py` as the entrypoint.
3. In app settings, add the secrets above.
4. Keep the app private/restricted while testing. Confirm Supabase reads and writes work before importing real contact data.
5. Add authentication and row-level security before any broader/public use.

## F. Daily workflow
1. Discover candidate companies or import a lawful CSV.
2. Open each source URL and verify the actual company and product.
3. Record export/expansion evidence and dates.
4. Score only what the evidence supports.
5. Call the prioritized list and record outcomes.
6. Review which score signals correlate with qualified conversations and conversions.

## Known limitations
- Search snippets can be stale, inaccurate, or point to directories rather than manufacturers.
- No automated phone validation, IEC verification, export-shipment verification, or Alibaba subscription lookup is included.
- Company and contact data coverage varies by industry and provider.
- Supabase keys are powerful; keep them server-side and use access controls.
- Current implementation uses a simple normalized company name for deduplication. Similar names, subsidiaries and legal suffixes can still require human review.
