import os
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Exporter Lead Intelligence", page_icon="🌍", layout="wide")

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "leads.db"

INDUSTRY_QUERIES = {
    "Engineering and auto parts": [
        'Maharashtra auto components manufacturer exporter contact',
        'Maharashtra engineering goods manufacturer exporter',
        'Pune Nashik Aurangabad automotive parts manufacturer export',
    ],
    "Textiles and garments": [
        'Maharashtra textile garment manufacturer exporter contact',
        'Ichalkaranji textile manufacturer exporter',
        'Mumbai garment manufacturer export business',
    ],
    "Food and spices": [
        'Maharashtra spices food processing manufacturer exporter',
        'Nashik food products exporter manufacturer',
        'Maharashtra spice exporter manufacturer contact',
    ],
    "Plastics and packaging": [
        'Maharashtra plastic packaging manufacturer exporter',
        'Mumbai Pune plastic products manufacturer export',
        'Maharashtra flexible packaging manufacturer exporter',
    ],
    "All manufacturing industries": [
        'Maharashtra manufacturing company exporter',
        'Maharashtra manufacturer international sales export',
        'India Maharashtra manufacturer export products company',
    ],
}

SCORE_FIELDS = {
    "product_fit": 0.25,
    "export_readiness": 0.20,
    "expansion_score": 0.25,
    "alibaba_opportunity": 0.20,
    "contact_quality": 0.10,
}

FIELDS = [
    "company_name", "city", "state", "website", "product_category",
    "business_type", "phone", "email", "contact_name", "contact_role",
    "export_evidence", "expansion_signal", "alibaba_status", "source_url",
    "product_fit", "export_readiness", "expansion_score",
    "alibaba_opportunity", "contact_quality", "call_status",
    "last_contacted", "next_action_date", "notes",
]

CALL_STATUSES = [
    "Needs research", "Not called", "Called - no answer", "Interested",
    "Follow-up needed", "Not a fit", "Already on Alibaba", "Do not contact",
    "Converted",
]

def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return os.getenv(name, default)

def normalize_name(name):
    raw = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", raw).strip()

def score_row(row):
    total = 0
    for field, weight in SCORE_FIELDS.items():
        try:
            val = max(0, min(100, int(float(row.get(field, 0) or 0))))
        except (ValueError, TypeError):
            val = 0
        total += val * weight
    return round(total)

def priority_for(score):
    if score >= 80:
        return "Call first"
    if score >= 60:
        return "Review and call"
    return "Research later"

def clean(v):
    if pd.isna(v):
        return ""
    return str(v).strip()

def supabase_client():
    url = secret("SUPABASE_URL")
    key = secret("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        st.error(f"Supabase connection could not be initialized: {exc}")
        return None

def init_local_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_company_name TEXT UNIQUE,
                company_name TEXT NOT NULL,
                city TEXT, state TEXT, website TEXT, product_category TEXT,
                business_type TEXT, phone TEXT, email TEXT, contact_name TEXT,
                contact_role TEXT, export_evidence TEXT, expansion_signal TEXT,
                alibaba_status TEXT, source_url TEXT,
                product_fit INTEGER DEFAULT 0, export_readiness INTEGER DEFAULT 0,
                expansion_score INTEGER DEFAULT 0, alibaba_opportunity INTEGER DEFAULT 0,
                contact_quality INTEGER DEFAULT 0, total_score INTEGER DEFAULT 0,
                priority TEXT DEFAULT 'Research later', call_status TEXT DEFAULT 'Needs research',
                last_contacted TEXT, next_action_date TEXT, notes TEXT,
                created_at TEXT, updated_at TEXT
            )
        """)
        con.commit()

def get_leads():
    sb = supabase_client()
    if sb:
        try:
            result = sb.table("leads").select("*").order("total_score", desc=True).execute()
            return pd.DataFrame(result.data or [])
        except Exception as exc:
            st.error(f"Supabase read failed. Check the table setup and credentials. Details: {exc}")
            return pd.DataFrame()
    init_local_db()
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql_query("SELECT * FROM leads", con)

def save_lead(record, lead_id=None):
    record = {k: clean(record.get(k, "")) for k in FIELDS}
    record["normalized_company_name"] = normalize_name(record["company_name"])
    if not record["company_name"]:
        raise ValueError("Company name is required.")
    for field in SCORE_FIELDS:
        try:
            record[field] = max(0, min(100, int(float(record[field] or 0))))
        except (ValueError, TypeError):
            record[field] = 0
    record["total_score"] = score_row(record)
    record["priority"] = priority_for(record["total_score"])
    record["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if not record.get("created_at"):
        record["created_at"] = record["updated_at"]

    sb = supabase_client()
    if sb:
        # normalized_company_name is UNIQUE in the SQL schema; upsert avoids most duplicate imports.
        try:
            sb.table("leads").upsert(record, on_conflict="normalized_company_name").execute()
            return
        except Exception as exc:
            raise RuntimeError(f"Supabase save failed: {exc}") from exc

    init_local_db()
    columns = list(record.keys())
    placeholders = ",".join(["?"] * len(columns))
    updates = ",".join([f"{c}=excluded.{c}" for c in columns if c != "normalized_company_name"])
    values = [record[c] for c in columns]
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            f"INSERT INTO leads ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(normalized_company_name) DO UPDATE SET {updates}",
            values,
        )
        con.commit()

def discover_with_brave(query, count=10):
    api_key = secret("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise RuntimeError("Add BRAVE_SEARCH_API_KEY to Streamlit secrets to enable web discovery.")
    response = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count, "country": "IN", "search_lang": "en"},
        timeout=25,
    )
    if response.status_code == 401:
        raise RuntimeError("Brave Search rejected the API key. Check the key and account access.")
    response.raise_for_status()
    data = response.json()
    return data.get("web", {}).get("results", [])

def candidate_from_result(item, industry):
    title = clean(item.get("title", ""))
    description = clean(item.get("description", ""))
    url = clean(item.get("url", ""))
    host = urlparse(url).netloc.lower()
    # Do not assume the search title is the registered legal company name.
    name = re.sub(r"\s*[-|:].*$", "", title).strip() or host
    return {
        "company_name": name[:250],
        "city": "",
        "state": "Maharashtra",
        "website": f"{urlparse(url).scheme}://{host}" if host else url,
        "product_category": industry,
        "business_type": "Unknown",
        "phone": "",
        "email": "",
        "contact_name": "",
        "contact_role": "",
        "export_evidence": "Unverified search result. Review source page before treating as fact. Search snippet: " + description[:1200],
        "expansion_signal": "",
        "alibaba_status": "Unknown",
        "source_url": url,
        "product_fit": 0,
        "export_readiness": 0,
        "expansion_score": 0,
        "alibaba_opportunity": 0,
        "contact_quality": 0,
        "call_status": "Needs research",
        "last_contacted": "",
        "next_action_date": "",
        "notes": "Discovered by web search. Verify company identity, Maharashtra location, product, export capacity and contact details.",
    }

def import_frame(df):
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for f in FIELDS:
        if f not in df.columns:
            df[f] = ""
    if "state" in df:
        df["state"] = df["state"].replace("", "Maharashtra")
    imported, skipped = 0, 0
    for _, row in df.iterrows():
        name = clean(row.get("company_name", ""))
        if not name:
            skipped += 1
            continue
        try:
            save_lead(row.to_dict())
            imported += 1
        except Exception:
            skipped += 1
    return imported, skipped

st.title("🌍 Exporter Lead Intelligence")
st.caption("Maharashtra-first prospecting for Alibaba.com subscriptions • Evidence-first qualification • Daily calling list")

sb_configured = bool(secret("SUPABASE_URL") and secret("SUPABASE_KEY"))
brave_configured = bool(secret("BRAVE_SEARCH_API_KEY"))
with st.sidebar:
    st.header("Workspace")
    page = st.radio("Navigate", [
        "Morning Calling List", "Free Lead Finder", "Discover Leads", "All Leads",
        "Add a Lead", "Import CSV", "Scoring Guide", "Setup & Status"
    ])
    st.divider()
    st.write("**Database:** " + ("Supabase cloud" if sb_configured else "Local SQLite"))
    st.write("**Web discovery:** " + ("Brave API configured" if brave_configured else "Not configured"))
    if not sb_configured:
        st.warning("Local SQLite is not persistent/reliable for a deployed cloud app. Configure Supabase before using this online.")

leads = get_leads()

if page == "Morning Calling List":
    st.subheader("Today's prioritized calling list")
    if leads.empty:
        st.info("No leads yet. Use Discover Leads, import a CSV, or add a company manually.")
    else:
        today = date.today().isoformat()
        active = leads.copy()
        if "call_status" not in active: active["call_status"] = "Needs research"
        active = active[~active["call_status"].isin(["Not a fit", "Do not contact", "Converted"])]
        if "next_action_date" not in active: active["next_action_date"] = ""
        active["next_action_date"] = active["next_action_date"].fillna("")
        due = active[(active["next_action_date"] == "") | (active["next_action_date"] <= today)]
        due = due.sort_values(["total_score", "expansion_score"], ascending=False)
        a,b,c,d = st.columns(4)
        a.metric("Total records", len(leads))
        b.metric("Due for review/call", len(due))
        c.metric("High priority", int((pd.to_numeric(due["total_score"], errors="coerce").fillna(0) >= 80).sum()))
        d.metric("Follow-ups due", int(((active["next_action_date"] != "") & (active["next_action_date"] <= today)).sum()))
        show_cols = [c for c in ["company_name","city","product_category","phone","contact_name","contact_role","total_score","priority","call_status","expansion_signal","source_url"] if c in due.columns]
        st.dataframe(due[show_cols], use_container_width=True, hide_index=True)
        st.download_button("Download calling list CSV", due[show_cols].to_csv(index=False).encode("utf-8"), "morning_calling_list.csv", "text/csv")
        st.caption("Search-discovered records start with zero scores until reviewed. A score is not proof of buying intent.")

elif page == "Free Lead Finder":
    st.subheader("Free lead research desk")
    st.write("Find potential Maharashtra exporters through free public search pages and directories, then import the companies into your lead database. No search API key required.")
    st.info("This free mode prepares targeted searches; it does not silently scrape search engines. Open results, verify the company, and import only relevant business records. Search results are not proof that a company exports.")

    sectors = {
        "Engineering and auto parts": ["auto components", "precision engineering", "castings", "CNC machining", "industrial machinery"],
        "Textiles and garments": ["textile manufacturer", "garment manufacturer", "home textiles", "fabric exporter", "technical textiles"],
        "Food and spices": ["spices manufacturer", "food processing", "dehydrated food", "agro products", "food exporter"],
        "Plastics and packaging": ["plastic packaging", "flexible packaging", "plastic moulding", "industrial packaging", "plastic products manufacturer"],
        "All manufacturing industries": ["manufacturer exporter", "industrial products manufacturer", "B2B manufacturer", "engineering goods", "industrial components"],
    }
    cities = ["All Maharashtra", "Mumbai", "Pune", "Nashik", "Thane", "Navi Mumbai", "Kolhapur", "Chhatrapati Sambhajinagar", "Nagpur", "Ichalkaranji", "Taloja", "Bhiwandi", "Ranjangaon", "Chakan", "Aurangabad MIDC"]
    chosen_sector = st.selectbox("Industry", list(sectors.keys()))
    chosen_city = st.selectbox("City / industrial area", cities)
    include_export_terms = st.checkbox("Prioritize searches mentioning exporter / exports", value=True)

    search_terms = []
    for product_term in sectors[chosen_sector]:
        location = "Maharashtra" if chosen_city == "All Maharashtra" else chosen_city
        suffix = " exporter exports international markets" if include_export_terms else " manufacturer company contact"
        search_terms.append(f'{product_term} {location}{suffix}')
    search_terms.extend([
        f'site:apeda.gov.in {chosen_city if chosen_city != "All Maharashtra" else "Maharashtra"} exporter directory',
        f'site:fieo.org {chosen_city if chosen_city != "All Maharashtra" else "Maharashtra"} exporter',
        f'{chosen_city if chosen_city != "All Maharashtra" else "Maharashtra"} manufacturer exporter contact official website',
    ])

    st.markdown("### Search links")
    st.caption("Open a search, inspect the company website, and capture only business contact details published for business enquiries.")
    for i, term in enumerate(search_terms, start=1):
        url = "https://www.google.com/search?q=" + quote_plus(term)
        st.markdown(f"{i}. [{term}]({url})")

    st.download_button(
        "Download this search plan as CSV",
        pd.DataFrame({"industry": [chosen_sector] * len(search_terms), "area": [chosen_city] * len(search_terms), "search_query": search_terms, "search_url": ["https://www.google.com/search?q=" + quote_plus(q) for q in search_terms]}).to_csv(index=False).encode("utf-8"),
        "free_maharashtra_exporter_search_plan.csv", "text/csv"
    )

    st.markdown("### Free public directories to check")
    st.markdown("""- [APEDA](https://apeda.gov.in/) for agricultural and processed-food exporters
- [FIEO](https://www.fieo.org/) for exporter resources and member information
- [EEPC India](https://www.eepcindia.org/) for engineering exporters
- [ExportersIndia](https://www.exportersindia.com/) for supplier discovery
- [IndiaMART](https://www.indiamart.com/) for manufacturers and suppliers (export status must be verified)""")

    st.markdown("### Add verified prospects")
    st.write("Use the import template to add multiple companies at once. Keep the original source URL so you can verify the record before calling.")
    template = pd.DataFrame(columns=FIELDS)
    st.download_button("Download lead import template", template.to_csv(index=False).encode("utf-8"), "exporter_lead_template.csv", "text/csv")
    upload_free = st.file_uploader("Import a CSV of researched companies", type=["csv"], key="free_finder_csv")
    if upload_free is not None:
        try:
            frame = pd.read_csv(upload_free, dtype=str).fillna("")
            st.dataframe(frame.head(10), use_container_width=True, hide_index=True)
            if st.button("Save researched companies", key="save_free_finder_csv"):
                imported, skipped = import_frame(frame)
                st.success(f"Saved/updated {imported} records. Skipped {skipped} empty or invalid records.")
                st.rerun()
        except Exception as exc:
            st.error(f"Could not read this CSV: {exc}")

elif page == "Discover Leads":
    st.subheader("Discover candidate companies")
    st.write("Uses a configured Brave Search API key to find public web pages. Results are candidates, not verified exporter records.")
    chosen = st.multiselect("Industries", list(INDUSTRY_QUERIES.keys()), default=[
        "Engineering and auto parts", "Textiles and garments", "Food and spices", "Plastics and packaging"
    ])
    max_results = st.slider("Results per query", min_value=5, max_value=20, value=10)
    if not brave_configured:
        st.warning("Web discovery is disabled until BRAVE_SEARCH_API_KEY is added in Streamlit secrets. You can still import CSV records.")
        st.markdown("[Brave Search API](https://brave.com/search/api/) provides a search API; review its pricing and terms before use.")
    if st.button("Search public web", disabled=not brave_configured or not chosen):
        existing = set(leads["normalized_company_name"].astype(str)) if not leads.empty and "normalized_company_name" in leads else set()
        seen_urls = set(leads["source_url"].astype(str)) if not leads.empty and "source_url" in leads else set()
        found = []
        errors = []
        with st.spinner("Searching public pages. Results will need manual review..."):
            for industry in chosen:
                for query in INDUSTRY_QUERIES[industry]:
                    try:
                        results = discover_with_brave(query, max_results)
                        for item in results:
                            cand = candidate_from_result(item, industry)
                            norm = normalize_name(cand["company_name"])
                            if not norm or norm in existing or cand["source_url"] in seen_urls:
                                continue
                            found.append(cand)
                            existing.add(norm)
                            seen_urls.add(cand["source_url"])
                    except Exception as exc:
                        errors.append(f"{query}: {exc}")
        st.session_state["discovered_candidates"] = found
        if errors:
            st.warning("Some searches failed: " + " | ".join(errors[:3]))
        st.success(f"Found {len(found)} candidate records for review.")
    candidates = st.session_state.get("discovered_candidates", [])
    if candidates:
        preview = pd.DataFrame(candidates)
        st.dataframe(preview[["company_name","product_category","website","source_url","export_evidence"]], use_container_width=True, hide_index=True)
        st.warning("Search snippets can be stale, misleading, or refer to directories rather than actual manufacturers. Review before calling.")
        if st.button("Save candidates to database"):
            imported, skipped = import_frame(preview)
            st.success(f"Saved/updated {imported} candidates. Skipped {skipped}. They remain marked 'Needs research'.")
            st.session_state["discovered_candidates"] = []
            st.rerun()

elif page == "All Leads":
    st.subheader("Lead database")
    if leads.empty:
        st.info("No leads yet.")
    else:
        q = st.text_input("Search company, product, city or contact")
        statuses = ["All"] + sorted(leads["call_status"].fillna("Needs research").unique().tolist())
        status_filter = st.selectbox("Call status", statuses)
        view = leads.copy()
        if q:
            mask = view.astype(str).apply(lambda col: col.str.contains(q, case=False, na=False)).any(axis=1)
            view = view[mask]
        if status_filter != "All":
            view = view[view["call_status"] == status_filter]
        st.dataframe(view.sort_values("total_score", ascending=False), use_container_width=True, hide_index=True)
        st.download_button("Export visible leads", view.to_csv(index=False).encode("utf-8"), "exporter_leads.csv", "text/csv")
        st.divider()
        st.markdown("### Record a call outcome")
        options = view.to_dict("records")
        if options:
            by_id = {str(r.get("id", r.get("normalized_company_name", r.get("company_name")))): r for r in options}
            keys = list(by_id.keys())
            chosen_key = st.selectbox("Company", keys, format_func=lambda k: by_id[k].get("company_name", "Unknown company"))
            rec = by_id[chosen_key]
            with st.form("call_outcome"):
                status = st.selectbox("Call status", CALL_STATUSES, index=CALL_STATUSES.index(rec.get("call_status")) if rec.get("call_status") in CALL_STATUSES else 0)
                next_date = st.date_input("Next action date", value=pd.to_datetime(rec["next_action_date"]).date() if rec.get("next_action_date") else None)
                notes = st.text_area("Call notes", value=rec.get("notes") or "")
                submit = st.form_submit_button("Save outcome")
                if submit:
                    update = {f: rec.get(f, "") for f in FIELDS}
                    update["call_status"] = status
                    update["next_action_date"] = next_date.isoformat() if next_date else ""
                    update["notes"] = notes
                    update["last_contacted"] = date.today().isoformat()
                    save_lead(update)
                    st.success("Call outcome saved.")
                    st.rerun()

elif page == "Add a Lead":
    st.subheader("Add a company")
    with st.form("add_lead"):
        left, right = st.columns(2)
        with left:
            company_name = st.text_input("Company name *")
            city = st.text_input("City")
            state = st.text_input("State", "Maharashtra")
            website = st.text_input("Website")
            product = st.text_input("Product category")
            business_type = st.selectbox("Business type", ["Manufacturer", "Manufacturer-exporter", "Merchant exporter", "Wholesaler", "Other", "Unknown"])
            phone = st.text_input("Business phone")
            email = st.text_input("Business email")
        with right:
            contact_name = st.text_input("Decision-maker")
            contact_role = st.text_input("Role")
            export_evidence = st.text_area("Export evidence (fact and source)")
            expansion_signal = st.text_area("Expansion signal (fact, date and source)")
            alibaba_status = st.selectbox("Alibaba status", ["Unknown", "Not identified", "Confirmed existing subscriber", "Former subscriber", "Interested / evaluating"])
            source_url = st.text_input("Source URL")
            notes = st.text_area("Notes")
        st.markdown("#### Score the evidence (0–100)")
        a,b,c,d,e = st.columns(5)
        pf = a.slider("Product fit", 0, 100, 0)
        er = b.slider("Export readiness", 0, 100, 0)
        ex = c.slider("Expansion signals", 0, 100, 0)
        ao = d.slider("Alibaba opportunity", 0, 100, 0)
        cq = e.slider("Contact quality", 0, 100, 0)
        submitted = st.form_submit_button("Save lead")
        if submitted:
            if not company_name.strip():
                st.error("Company name is required.")
            else:
                record = locals()
                record = {
                    "company_name": company_name, "city": city, "state": state, "website": website,
                    "product_category": product, "business_type": business_type, "phone": phone,
                    "email": email, "contact_name": contact_name, "contact_role": contact_role,
                    "export_evidence": export_evidence, "expansion_signal": expansion_signal,
                    "alibaba_status": alibaba_status, "source_url": source_url,
                    "product_fit": pf, "export_readiness": er, "expansion_score": ex,
                    "alibaba_opportunity": ao, "contact_quality": cq, "call_status": "Not called",
                    "last_contacted": "", "next_action_date": "", "notes": notes
                }
                try:
                    save_lead(record)
                    st.success("Lead saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

elif page == "Import CSV":
    st.subheader("Import company data")
    template_cols = FIELDS
    st.download_button("Download CSV template", pd.DataFrame(columns=template_cols).to_csv(index=False).encode("utf-8"), "exporter_lead_template.csv", "text/csv")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])
    if uploaded:
        frame = pd.read_csv(uploaded, dtype=str).fillna("")
        st.dataframe(frame.head(10), use_container_width=True, hide_index=True)
        if st.button("Import / update records"):
            imported, skipped = import_frame(frame)
            st.success(f"Saved/updated {imported} records. Skipped {skipped} empty or invalid records.")
            st.rerun()

elif page == "Scoring Guide":
    st.subheader("Scoring guide")
    st.markdown("""
    | Criterion | Weight | What counts as evidence |
    |---|---:|---|
    | Product fit | 25% | Wholesale-ready manufactured product suited to B2B export |
    | Export readiness | 20% | Export history, capability, certifications, or credible export plan |
    | Expansion signals | 25% | Recent market expansion, export hiring, trade-fair activity, new overseas contract |
    | Alibaba opportunity | 20% | Plausible unmet need; existing subscription status checked where possible |
    | Contact quality | 10% | Business contact and decision-maker verified |

    **80–100:** call first · **60–79:** review and call · **Below 60:** research later.

    These are initial heuristics, not validated conversion probabilities. Unknown evidence should score zero until researched.
    """)
    st.info("Keep source URLs and dates. AI can summarize evidence, but a person should verify the claim before calling.")

elif page == "Setup & Status":
    st.subheader("Deployment and integration status")
    st.write("Free Lead Finder works without a paid API. Brave Search discovery remains optional. For a persistent online app, configure Supabase.")
    st.markdown("""
    **Required for online persistence**
    - `SUPABASE_URL`
    - `SUPABASE_KEY` (use a restricted key appropriate to the app; never commit secrets to GitHub)

    **Optional for API-based web discovery**
    - `BRAVE_SEARCH_API_KEY`

    **Free alternative**
    - Open **Free Lead Finder** to generate targeted public-search links and import researched companies without an API key.

    Add these under **Streamlit Community Cloud → App → Settings → Secrets**.
    """)
    st.write("Supabase:", "Configured" if sb_configured else "Not configured")
    st.write("Brave Search:", "Configured" if brave_configured else "Not configured")
    st.markdown("[Supabase](https://supabase.com/) · [Streamlit Community Cloud](https://share.streamlit.io/) · [Brave Search API](https://brave.com/search/api/)")
    st.warning("Without Supabase, hosted Streamlit storage may reset or not be shared reliably. Do not use local SQLite as the permanent database for the online version.")

st.divider()
st.caption("MVP v2.1 • Free Lead Finder included • Search results are unverified candidates, not confirmed exporters or proof of buying intent. Respect source terms, privacy requirements, and contact opt-outs.")
