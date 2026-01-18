import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from datetime import date, datetime, timedelta
import json
from fpdf import FPDF
import base64
import difflib
import urllib.parse
import time
import re

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma", layout="centered", page_icon="💊")

# --- CUSTOM CSS: DARK MODE FINTECH STYLE ---
st.markdown("""
    <style>
    /* 1. Main Background */
    .stApp {
        background-color: #0e1117;
        color: #fafafa;
    }
    
    /* 2. Metric Cards */
    div[data-testid="metric-container"] {
        background-color: #1e1e1e;
        border: 1px solid #333;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        transition: transform 0.2s;
    }
    div[data-testid="metric-container"]:hover {
        transform: scale(1.02);
        border-color: #555;
    }
    div[data-testid="metric-container"] label {
        color: #a0a0a0;
        font-size: 0.9rem;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #ffffff;
        font-weight: 700;
    }
    
    /* 3. Electric Buttons */
    .stButton>button {
        width: 100%;
        height: 4em;
        background-color: #262730;
        color: #ffffff;
        border: 1px solid #4b4b4b;
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #2979ff;
        border-color: #2979ff;
        color: #ffffff;
        box-shadow: 0 0 15px rgba(41, 121, 255, 0.4);
        transform: translateY(-2px);
    }
    
    /* 4. Splash Screen Animation */
    .splash-container {
        display: flex;
        justify-content: center;
        align-items: center;
        height: 70vh;
        flex-direction: column;
        animation: fadeOut 4s forwards;
    }
    .splash-container img {
        width: 150px; 
        margin-bottom: 20px;
        border-radius: 20px;
        box-shadow: 0 0 30px rgba(41, 121, 255, 0.2);
    }
    .splash-sub {
        font-size: 24px;
        color: #a0a0a0;
        font-weight: 600;
        letter-spacing: 2px;
        text-transform: uppercase;
    }
    @keyframes fadeOut {
        0% { opacity: 0; transform: scale(0.8); }
        15% { opacity: 1; transform: scale(1); }
        85% { opacity: 1; transform: scale(1); } 
        100% { opacity: 0; transform: scale(1.1); }
    }
    </style>
""", unsafe_allow_html=True)

# --- 1. SPLASH SCREEN (LOGO) ---
def show_splash_screen():
    if "splash_shown" not in st.session_state:
        splash = st.empty()
        with splash.container():
            # Your correct RAW image link
            logo_url = "https://raw.githubusercontent.com/gautam-pharma-ledger/ledger-app/main/Photoroom-20260102_114853282.png"
            
            st.markdown(f"""
            <div class="splash-container">
                <img src="{logo_url}">
                <div class="splash-sub">Gautam Pharma</div>
            </div>
            """, unsafe_allow_html=True)
            time.sleep(4)
        splash.empty()
        st.session_state["splash_shown"] = True

# --- 2. CONNECTION & UTILS ---
@st.cache_resource
def get_gsheet_client():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(credentials)
    except Exception as e: return None

@st.cache_resource
def get_sheet_object():
    client = get_gsheet_client()
    if client:
        try: return client.open("Gautam_Pharma_Ledger")
        except: return None
    return None

@st.cache_data(ttl=5)
def fetch_sheet_data(sheet_name):
    try:
        sh = get_sheet_object()
        if not sh: return pd.DataFrame()
        data = sh.worksheet(sheet_name).get_all_records()
        return pd.DataFrame(data)
    except: return pd.DataFrame()

def get_next_code(current_codes, prefix):
    max_num = 0
    for code in current_codes:
        code_str = str(code).strip().upper()
        if code_str.startswith(prefix):
            match = re.search(r'\d+', code_str)
            if match:
                num = int(match.group())
                if num > max_num: max_num = num
    return f"{prefix}{max_num + 1}"

def get_master_map():
    master = fetch_sheet_data("Party_Master")
    mapping = {}
    codes_list = []
    if not master.empty:
        for _, r in master.iterrows():
            name = str(r.get("Name", "")).strip()
            code = str(r.get("Code", "")).strip()
            if name: mapping[name] = code
            if code: codes_list.append(code)
    return mapping, codes_list

def get_all_party_names_display():
    mapping, _ = get_master_map()
    for sheet in ["CustomerDues", "PaymentsReceived", "GoodsReceived", "PaymentsToSuppliers"]:
        df = fetch_sheet_data(sheet)
        col = "Party" if "Party" in df.columns else "Supplier"
        if not df.empty and col in df.columns:
            for name in df[col].unique():
                if name not in mapping: mapping[name] = ""
    display_list = []
    for name in sorted(mapping.keys()):
        code = mapping[name]
        display_list.append(f"{name} ({code})" if code else name)
    return display_list

def extract_name_display(display_str):
    if "(" in display_str and ")" in display_str:
        return display_str.split(" (")[0].strip()
    return display_str.strip()

def clean_amount(val):
    try: return float(str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip())
    except: return 0.0

def parse_date(date_str):
    try: return pd.to_datetime(date_str).date()
    except: return None

def smart_match_party(scanned_name, existing_names):
    matches = difflib.get_close_matches(scanned_name, existing_names, n=1, cutoff=0.6)
    return matches[0] if matches else scanned_name

# --- 3. AI HELPERS ---
def extract_json_from_text(text):
    try:
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1: return json.loads(text[start:end])
        return None
    except: return None

def extract_single_party_ledger(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        prompt = """Analyze image of SINGLE PARTY ledger.
        1. Find Party Name.
        2. Find Opening Balance (B/F).
        3. Extract table (Date, Particulars, Debit, Credit).
        Return JSON: {"PartyName": "Name", "OpeningBalance": 0.0, "Transactions": [{"Date": "YYYY-MM-DD", "Particulars": "Desc", "Debit": 0.0, "Credit": 0.0}]}"""
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}])
        return extract_json_from_text(response.choices[0].message.content)
    except: return None

def run_daily_scan_extraction(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        prompt = """Analyze daily journal page. 
        1. Extract the Date written on the page.
        2. Map entries to: CustomerDues (Sales), PaymentsReceived, GoodsReceived (Purchases), PaymentsToSuppliers.
        Return JSON: { "Date": "YYYY-MM-DD", 
        "CustomerDues": [{"Party": "Name", "Amount": 0}], 
        "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], 
        "GoodsReceived": [{"Supplier": "Name", "Items": "Desc", "Amount": 0}], 
        "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}] }"""
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}])
        return extract_json_from_text(response.choices[0].message.content)
    except: return None

# --- 4. PDF ---
def generate_pdf(party, df, start, end):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "Gautam Pharma", ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(190, 10, f"Statement: {party} ({start} to {end})", ln=True, align='C')
    pdf.ln(5)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(25, 8, "Date", 1, 0, 'C', 1)
    pdf.cell(85, 8, "Particulars", 1, 0, 'C', 1)
    pdf.cell(25, 8, "Debit", 1, 0, 'C', 1)
    pdf.cell(25, 8, "Credit", 1, 0, 'C', 1)
    pdf.cell(30, 8, "Balance", 1, 1, 'C', 1)
    bal = 0
    pdf.set_font("Arial", '', 9)
    for _, r in df.iterrows():
        dr, cr = r['Debit'], r['Credit']
        bal += (dr - cr)
        pdf.cell(25, 7, str(r['Date']), 1)
        pdf.cell(85, 7, str(r['Description'])[:40], 1)
        pdf.cell(25, 7, f"{dr:,.2f}", 1)
        pdf.cell(25, 7, f"{cr:,.2f}", 1)
        pdf.cell(30, 7, f"{bal:,.2f}", 1, 1)
    return pdf.output(dest='S').encode('latin-1')

# --- 5. NAV HELPER ---
def go_to(page):
    st.session_state['page'] = page
    st.rerun()

# --- 6. SCREENS ---

def screen_home():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    supp_pay = fetch_sheet_data("PaymentsToSuppliers")
    
    total_receivable = 0
    total_payable = 0
    
    if not dues.empty and not pymt.empty:
        sales = dues.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        cols = pymt.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        all_cust = sales.index.union(cols.index)
        for p in all_cust:
            bal = sales.get(p, 0) - cols.get(p, 0)
            if bal > 0: total_receivable += bal
            
    if not goods.empty and not supp_pay.empty:
        purchases = goods.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        paid_out = supp_pay.groupby("Supplier")["Amount"].apply(lambda x: x.apply(clean_amount).sum())
        all_supp = purchases.index.union(paid_out.index)
        for s in all_supp:
            bal = purchases.get(s, 0) - paid_out.get(s, 0)
            if bal > 0: total_payable += bal 

    net = total_receivable - total_payable
    
    st.markdown("### 📊 Market Position")
    c1, c2 = st.columns(2)
    c1.metric("🟢 Receivable", f"₹{total_receivable:,.0f}")
    c2.metric("🔴 Payable", f"₹{total_payable:,.0f}")
    st.metric("Net Position", f"₹{net:,.0f}")
    st.markdown("---")
    
    # --- UPDATED BUTTON LAYOUT WITH DAY BOOK ---
    c1, c2, c3 = st.columns(3)
    if c1.button("📝\nEntry"): go_to('manual')
    if c2.button("📅\nDay Book"): go_to('day_book')
    if c3.button("📒\nLedger"): go_to('ledger')
    
    c4, c5, c6 = st.columns(3)
    if c4.button("📸\nScan"): go_to('scan_daily')
    if c5.button("🔔\nRemind"): go_to('reminders')
    if c6.button("⚙️\nTools"): go_to('tools')

def screen_day_book():
    st.markdown("### 📅 Day Book (Roznamcha)")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    view_date = st.date_input("Select Date", date.today())
    
    with st.spinner("Fetching Day's Data..."):
        sales = fetch_sheet_data("CustomerDues")
        received = fetch_sheet_data("PaymentsReceived")
        paid = fetch_sheet_data("PaymentsToSuppliers")
        purchases = fetch_sheet_data("GoodsReceived")

    def filter_by_date(df):
        if df.empty or "Date" not in df.columns: return pd.DataFrame()
        df["_dt"] = pd.to_datetime(df["Date"], errors='coerce', dayfirst=True).dt.date
        filtered = df[df["_dt"] == view_date].copy()
        return filtered.drop(columns=["_dt"])

    d_sales = filter_by_date(sales)
    d_received = filter_by_date(received)
    d_paid = filter_by_date(paid)
    d_purchases = filter_by_date(purchases)

    t_sales = d_sales["Amount"].apply(clean_amount).sum() if not d_sales.empty else 0
    t_rec = d_received["Amount"].apply(clean_amount).sum() if not d_received.empty else 0
    t_paid = d_paid["Amount"].apply(clean_amount).sum() if not d_paid.empty else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Sales", f"₹{t_sales:,.0f}")
    m2.metric("Total Received", f"₹{t_rec:,.0f}")
    m3.metric("Total Paid", f"₹{t_paid:,.0f}")
    st.markdown("---")

    def render_section(title, df, sheet_name, color):
        if df.empty: return
        st.markdown(f"#### {title}")
        df["Date"] = df["Date"].astype(str)
        edited = st.data_editor(
            df,
            key=f"editor_{sheet_name}",
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "Date": st.column_config.TextColumn("Date", help="DD/MM/YYYY"),
                "Amount": st.column_config.NumberColumn("Amount", format="₹%.2f")
            }
        )
        if st.button(f"💾 Save {title}", key=f"save_{sheet_name}"):
            try:
                full_df = fetch_sheet_data(sheet_name)
                full_df["_dt"] = pd.to_datetime(full_df["Date"], errors='coerce', dayfirst=True).dt.date
                kept_rows = full_df[full_df["_dt"] != view_date].drop(columns=["_dt"])
                
                edited["Date"] = pd.to_datetime(edited["Date"], dayfirst=True).dt.strftime("%Y-%m-%d")
                final_df = pd.concat([kept_rows, edited], ignore_index=True)
                
                sh = get_sheet_object()
                ws = sh.worksheet(sheet_name)
                ws.clear()
                ws.update([final_df.columns.tolist()] + final_df.astype(str).values.tolist())
                st.success(f"Updated {title}!"); st.cache_data.clear(); time.sleep(1); st.rerun()
            except Exception as e:
                st.error(f"Error saving: {str(e)}")

    render_section("🔵 Sales (Bills)", d_sales, "CustomerDues", "blue")
    render_section("🟢 Payments Received", d_received, "PaymentsReceived", "green")
    render_section("🔴 Paid to Suppliers", d_paid, "PaymentsToSuppliers", "red")
    render_section("🟠 Purchases (Goods)", d_purchases, "GoodsReceived", "orange")

    if d_sales.empty and d_received.empty and d_paid.empty and d_purchases.empty:
        st.info(f"No transactions found for {view_date.strftime('%d %b %Y')}")

def screen_reminders():
    st.markdown("### 🔔 Payment Reminders (WhatsApp)")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    with st.spinner("Analyzing..."):
        dues = fetch_sheet_data("CustomerDues")
        pymt = fetch_sheet_data("PaymentsReceived")
        mapping, _ = get_master_map()
        phones = {}
        master = fetch_sheet_data("Party_Master")
        if not master.empty:
            for _, r in master.iterrows():
                phones[r["Name"]] = str(r.get("Phone", ""))

        bals = {}
        if not dues.empty:
            for _, r in dues.iterrows():
                p = r["Party"]
                bals[p] = bals.get(p, 0) + clean_amount(r["Amount"])
        if not pymt.empty:
            for _, r in pymt.iterrows():
                p = r["Party"]
                bals[p] = bals.get(p, 0) - clean_amount(r["Amount"])
                
        data = []
        for p, amt in bals.items():
            if amt != 0:
                code = mapping.get(p, "")
                display = f"{p} ({code})" if code else p
                data.append({"Party": display, "Balance": amt, "Phone": phones.get(p, "")})
                
    st.write("Sort By:")
    s1, s2, s3, s4 = st.columns(4)
    if s1.button("High-Low"): st.session_state['sort_mode'] = 'High-Low'; st.rerun()
    if s2.button("Low-High"): st.session_state['sort_mode'] = 'Low-High'; st.rerun()
    if s3.button("A-Z"): st.session_state['sort_mode'] = 'A-Z'; st.rerun()
    if s4.button("Z-A"): st.session_state['sort_mode'] = 'Z-A'; st.rerun()
    
    mode = st.session_state.get('sort_mode', 'High-Low')
    if mode == 'High-Low': data.sort(key=lambda x: x['Balance'], reverse=True)
    elif mode == 'Low-High': data.sort(key=lambda x: x['Balance'])
    elif mode == 'A-Z': data.sort(key=lambda x: x['Party'])
    elif mode == 'Z-A': data.sort(key=lambda x: x['Party'], reverse=True)

    st.markdown("---")
    df_disp = pd.DataFrame(data)[["Party", "Balance", "Phone"]]
    df_disp["Select"] = False
    
    edited = st.data_editor(df_disp, column_config={"Select": st.column_config.CheckboxColumn(default=False)}, hide_index=True, use_container_width=True)
    sel = edited[edited["Select"] == True]
    if not sel.empty:
        st.success(f"Selected {len(sel)} parties.")
        st.markdown("### 💬 Tap to Open WhatsApp")
        for _, row in sel.iterrows():
            p_display = row["Party"]
            p_raw = extract_name_display(p_display)
            b = row["Balance"]
            ph = row["Phone"]
            msg = f"Hello {p_raw}, Your pending balance with Gautam Pharma is Rs {b:,.0f}. Please pay soon."
            
            # --- WHATSAPP LINK LOGIC ---
            if ph:
                clean = re.sub(r'\D', '', str(ph))
                if len(clean) == 10: clean = "91" + clean
                link = f"https://wa.me/{clean}?text={urllib.parse.quote(msg)}"
                st.link_button(f"📲 WhatsApp {p_raw}", link, use_container_width=True)
            else:
                # Fallback if no number is saved
                link = f"https://wa.me/?text={urllib.parse.quote(msg)}"
                st.link_button(f"📲 WhatsApp {p_raw} (No Number Saved)", link, use_container_width=True)

def screen_tools():
    st.markdown("### ⚙️ Admin Tools")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔄 Merge", "✏️ Edit Txn", "📇 Party & Codes", "🧨 Reset"])
    
    with tab1:
        st.write("Combine two parties.")
        parties = get_all_party_names_display()
        c1, c2 = st.columns(2)
        old = c1.selectbox("Wrong Name", parties, index=None, placeholder="Search...")
        new = c2.selectbox("Correct Name", parties, index=None, placeholder="Search...")
        if st.button("Merge") and old and new:
            old_raw = extract_name_display(old)
            new_raw = extract_name_display(new)
            sh = get_sheet_object()
            count = 0
            for s in ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"]:
                try:
                    ws = sh.worksheet(s)
                    vals = ws.get_all_values()
                    head = vals[0]
                    col = -1
                    if "Party" in head: col = head.index("Party")
                    elif "Supplier" in head: col = head.index("Supplier")
                    if col != -1:
                        ups = []
                        for i, r in enumerate(vals):
                            if i>0 and r[col] == old_raw:
                                ups.append({"range": f"{chr(65+col)}{i+1}", "values": [[new_raw]]})
                                count += 1
                        if ups: ws.batch_update(ups)
                except: pass
            st.success(f"Merged {count} entries!"); st.cache_data.clear()

    with tab2:
        st.write("### Edit Transactions")
        st.info("You can type dates as DD/MM/YYYY here.")
        
        sheet = st.selectbox("Sheet", ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"])
        raw_parties = [extract_name_display(p) for p in get_all_party_names_display()]
        f_party = st.selectbox("Filter", ["All"] + sorted(list(set(raw_parties))))
        
        c1, c2 = st.columns(2)
        s_date = c1.date_input("Start", date.today().replace(day=1))
        e_date = c2.date_input("End", date.today())
        
        if st.button("Load Data"):
            df = fetch_sheet_data(sheet)
            if not df.empty:
                if f_party != "All":
                    col = "Party" if "Party" in df.columns else "Supplier"
                    if col in df.columns: df = df[df[col] == f_party]
                if "Date" in df.columns:
                    df["D"] = pd.to_datetime(df["Date"], errors='coerce', dayfirst=True).dt.date
                    df = df[(df["D"] >= s_date) & (df["D"] <= e_date)]
                    df = df.drop(columns=["D"])
                st.session_state['edit_df'] = df
                st.session_state['edit_s'] = sheet
        
        if 'edit_df' in st.session_state:
            df_display = st.session_state['edit_df'].copy()
            if "Date" in df_display.columns:
                df_display["Date"] = df_display["Date"].astype(str)

            edited = st.data_editor(
                df_display,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Date": st.column_config.TextColumn("Date", help="Type as DD/MM/YYYY"),
                    "Amount": st.column_config.NumberColumn("Amount", format="₹%.2f")
                }
            )

            if st.button("💾 Save Changes"):
                try:
                    if f_party == "All":
                        if "Date" in edited.columns:
                            edited["Date"] = pd.to_datetime(edited["Date"], dayfirst=True).dt.strftime("%Y-%m-%d")
                        sh = get_sheet_object()
                        ws = sh.worksheet(st.session_state['edit_s'])
                        ws.clear()
                        ws.update([edited.columns.tolist()] + edited.astype(str).values.tolist())
                        st.success("✅ Updated successfully!"); st.cache_data.clear()
                    else: 
                        st.warning("⚠️ Safety Lock: Please select 'All' in Filter to delete or save changes.")
                except Exception as e: st.error(f"Error: {str(e)}")

    with tab3:
        st.write("Edit Codes, Phones & Addresses.")
        df_master = fetch_sheet_data("Party_Master")
        if "Code" not in df_master.columns: df_master["Code"] = ""
        
        all_raw = sorted(list(set([extract_name_display(p) for p in get_all_party_names_display()])))
        if not df_master.empty: existing = df_master["Name"].astype(str).tolist()
        else: existing = []; df_master = pd.DataFrame(columns=["Name", "Code", "Type", "Phone", "Address"])
        
        new_rows = []
        current_codes = df_master["Code"].tolist()
        cust_set = set()
        for s in ["CustomerDues", "PaymentsReceived"]:
            d = fetch_sheet_data(s)
            if not d.empty and "Party" in d.columns: cust_set.update(d["Party"].unique())
            
        for name in all_raw:
            if name not in existing:
                prefix = "R" if name in cust_set else "S"
                new_code = get_next_code(current_codes, prefix)
                current_codes.append(new_code)
                new_rows.append({"Name": name, "Code": new_code, "Type": "Customer" if prefix=="R" else "Supplier", "Phone": "", "Address": ""})
        
        if new_rows: df_master = pd.concat([df_master, pd.DataFrame(new_rows)], ignore_index=True)
        
        edited = st.data_editor(df_master, num_rows="dynamic", use_container_width=True)
        if st.button("Save Master"):
            sh = get_sheet_object()
            ws = sh.worksheet("Party_Master")
            ws.clear()
            ws.update([edited.columns.tolist()] + edited.astype(str).values.tolist())
            st.success("Saved!"); st.cache_data.clear()

    with tab4:
        st.error("⚠️ FACTORY RESET")
        if st.button("🧨 Delete All", disabled=(st.text_input("Type WIPE DATA") != "WIPE DATA")):
            sh = get_sheet_object()
            sheets = {"CustomerDues": ["Date","Party","Amount"], "PaymentsReceived": ["Date","Party","Amount","Mode"], 
                      "PaymentsToSuppliers": ["Date","Supplier","Amount","Mode"], "GoodsReceived": ["Date","Supplier","Items","Amount"],
                      "Party_Master": ["Name","Code","Type","Phone","Address"]}
            for s, h in sheets.items():
                try: ws = sh.worksheet(s); ws.clear(); ws.update(range_name="A1", values=[h])
                except: pass
            st.success("Reset!"); st.cache_data.clear(); time.sleep(2); st.rerun()

def screen_digitize_ledger():
    st.markdown("### 📂 Digitize Old Ledger")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    img = st.file_uploader("Upload Image", type=['jpg', 'png'])
    if img and st.button("🚀 Process"):
        with st.spinner("AI Reading..."):
            data = extract_single_party_ledger(img.read())
            if data: st.session_state['hist_data'] = data; st.rerun()
            
    if 'hist_data' in st.session_state:
        data = st.session_state['hist_data']
        with st.form("save_hist"):
            scanned = data.get("PartyName", "")
            mapping, codes_list = get_master_map()
            existing_names = list(mapping.keys())
            final_raw = smart_match_party(scanned, existing_names)
            existing_code = mapping.get(final_raw, "")
            
            if not existing_code:
                new_code = get_next_code(codes_list, "R")
                display_val = f"{final_raw} (New: {new_code})"
            else:
                display_val = f"{final_raw} ({existing_code})"
                
            st.write("Party Name & Code:")
            name_input = st.text_input("Edit Name (AI Guess)", value=display_val)
            c1, c2 = st.columns(2)
            op = c1.number_input("Opening Bal", value=float(data.get("OpeningBalance", 0)))
            dt = c2.date_input("Date (Opening Bal)", date.today().replace(month=4, day=1))
            
            df = pd.DataFrame(data.get("Transactions", []))
            for c in ["Date", "Particulars", "Debit", "Credit"]: 
                if c not in df.columns: df[c] = ""
            df["Date"] = df["Date"].astype(str)
            df["Particulars"] = df["Particulars"].astype(str)
            df["Debit"] = df["Debit"].apply(clean_amount)
            df["Credit"] = df["Credit"].apply(clean_amount)

            st.write("### ✏️ Edit Transactions (DD/MM/YYYY)")
            edited = st.data_editor(
                df, 
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "Date": st.column_config.TextColumn("Date (DD/MM/YYYY)", help="Type strictly as DD/MM/YYYY"),
                    "Particulars": st.column_config.TextColumn("Particulars"),
                    "Debit": st.column_config.NumberColumn("Debit", min_value=0, format="₹%.2f"),
                    "Credit": st.column_config.NumberColumn("Credit", min_value=0, format="₹%.2f")
                }
            )
            
            if st.form_submit_button("Save"):
                if "(" in name_input: p_raw = name_input.split("(")[0].strip()
                else: p_raw = name_input.strip()
                sh = get_sheet_object()
                if p_raw not in existing_names:
                    master = fetch_sheet_data("Party_Master")
                    curr_codes = master["Code"].tolist() if "Code" in master.columns else []
                    final_code = get_next_code(curr_codes, "R")
                    sh.worksheet("Party_Master").append_row([p_raw, final_code, "Customer", "", ""])
                
                s_rows, p_rows = [], []
                def fix_date(d_str):
                    try: return pd.to_datetime(d_str, dayfirst=True).strftime("%Y-%m-%d")
                    except: return str(date.today())

                if op > 0: s_rows.append([str(dt), p_raw, op])
                for _, r in edited.iterrows():
                    raw_date = str(r.get("Date", ""))
                    final_date = fix_date(raw_date)
                    dr, cr = clean_amount(r.get("Debit", 0)), clean_amount(r.get("Credit", 0))
                    if dr > 0: s_rows.append([final_date, p_raw, dr])
                    if cr > 0: p_rows.append([final_date, p_raw, cr, "Old Ledger"])
                
                if s_rows: sh.worksheet("CustomerDues").append_rows(s_rows)
                if p_rows: sh.worksheet("PaymentsReceived").append_rows(p_rows)
                st.success("Saved!"); st.cache_data.clear(); del st.session_state['hist_data']

def screen_manual():
    st.markdown("### 📝 New Entry")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    parties_display = get_all_party_names_display()
    
    with st.form("manual"):
        c1, c2 = st.columns(2)
        d_val = c1.date_input("Date", date.today())
        e_type = c2.selectbox("Type", ["Sale (Bill)", "Payment Received", "Supplier Payment", "Purchase (Goods)"])
        c3, c4 = st.columns(2)
        p_in = c3.selectbox("Party", ["Add New"] + parties_display, index=None, placeholder="Search...")
        new_p_name = None
        if p_in == "Add New": 
            new_p_name = c3.text_input("New Name")
            party_final = new_p_name
        elif p_in:
            party_final = extract_name_display(p_in)
        else: party_final = None
        amt = c4.number_input("Amount", min_value=0.0)
        extra = ""
        if e_type in ["Payment Received", "Supplier Payment"]: extra = st.selectbox("Mode", ["Cash", "UPI", "Cheque"])
        elif e_type == "Purchase (Goods)": extra = st.text_input("Items", "Goods")

        if st.form_submit_button("Save"):
            if not party_final or amt == 0: st.error("Invalid Input"); st.stop()
            sh = get_sheet_object()
            if p_in == "Add New":
                master = fetch_sheet_data("Party_Master")
                codes = master["Code"].tolist() if "Code" in master.columns else []
                prefix = "S" if e_type in ["Supplier Payment", "Purchase (Goods)"] else "R"
                type_lbl = "Supplier" if prefix == "S" else "Customer"
                new_code = get_next_code(codes, prefix)
                sh.worksheet("Party_Master").append_row([party_final, new_code, type_lbl, "", ""])
                st.toast(f"Created {type_lbl}: {party_final} ({new_code})")
            try:
                if e_type == "Sale (Bill)": sh.worksheet("CustomerDues").append_row([str(d_val), party_final, amt])
                elif e_type == "Payment Received": sh.worksheet("PaymentsReceived").append_row([str(d_val), party_final, amt, extra])
                elif e_type == "Supplier Payment": sh.worksheet("PaymentsToSuppliers").append_row([str(d_val), party_final, amt, extra])
                elif e_type == "Purchase (Goods)": sh.worksheet("GoodsReceived").append_row([str(d_val), party_final, extra, amt])
                st.success("Saved!"); st.cache_data.clear()
            except Exception as e: st.error(str(e))

def screen_ledger():
    st.markdown("### 📒 Party Ledger")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    sel_display = st.selectbox("Party", get_all_party_names_display(), index=None, placeholder="Search...")
    sel_party = extract_name_display(sel_display) if sel_display else None
    
    if 'l_s' not in st.session_state: st.session_state['l_s'] = date.today().replace(day=1)
    if 'l_e' not in st.session_state: st.session_state['l_e'] = date.today()
    
    c1, c2, c3 = st.columns(3)
    if c1.button("This Month"): st.session_state['l_s'] = date.today().replace(day=1); st.session_state['l_e'] = date.today(); st.rerun()
    if c2.button("Last Month"): 
        first = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        st.session_state['l_s'] = first; st.session_state['l_e'] = date.today().replace(day=1) - timedelta(days=1); st.rerun()
    if c3.button("All Time"): st.session_state['l_s'] = date(2023,1,1); st.session_state['l_e'] = date.today(); st.rerun()

    d1, d2 = st.columns(2)
    s = d1.date_input("From", st.session_state['l_s'])
    e = d2.date_input("To", st.session_state['l_e'])
    
    if st.button("🔎 Show Statement", type="primary") and sel_party:
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        goods_df = fetch_sheet_data("GoodsReceived")
        supp_pay_df = fetch_sheet_data("PaymentsToSuppliers")
        
        ledger = []
        if not d_df.empty:
            sub = d_df[d_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": "Sale", "Dr": clean_amount(r['Amount']), "Cr": 0})
        if not p_df.empty:
            sub = p_df[p_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": f"Rx ({r.get('Mode','')})", "Dr": 0, "Cr": clean_amount(r['Amount'])})
        if not goods_df.empty:
            sub = goods_df[goods_df['Supplier'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": f"Purchase ({r.get('Items','')})", "Dr": 0, "Cr": clean_amount(r['Amount'])})
        if not supp_pay_df.empty:
            sub = supp_pay_df[supp_pay_df['Supplier'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e: ledger.append({"Date": r_date, "Desc": f"Paid Supplier ({r.get('Mode','')})", "Dr": clean_amount(r['Amount']), "Cr": 0})
        
        if ledger:
            df = pd.DataFrame(ledger).sort_values('Date')
            df.columns = ["Date", "Description", "Debit", "Credit"]
            bal = df['Debit'].sum() - df['Credit'].sum()
            st.dataframe(df, use_container_width=True)
            status = "Receivable" if bal > 0 else "Payable"
            st.metric("Net Balance", f"₹{abs(bal):,.2f}", status)
            
            pdf_bytes = generate_pdf(sel_party, df, s, e)
            st.download_button("📄 PDF Statement", pdf_bytes, "stmt.pdf", "application/pdf", use_container_width=True)
            
            # --- LEDGER: WHATSAPP LINK ---
            msg = f"Hello {sel_party}, Balance: {bal}"
            enc_msg = urllib.parse.quote(msg)
            st.link_button("💬 WhatsApp", f"https://wa.me/?text={enc_msg}", use_container_width=True)

        else: st.info("No Transactions Found.")

def screen_scan_daily():
    st.markdown("### 📸 Daily Scan")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    img = st.file_uploader("Journal Page", type=['jpg', 'png'])
    if img and st.button("Extract"):
        with st.spinner("AI Reading..."):
            data = run_daily_scan_extraction(img.read())
            if data: st.session_state['daily_data'] = data; st.rerun()
            
    if 'daily_data' in st.session_state:
        data = st.session_state['daily_data']
        with st.form("daily_save"):
            st.write("### Review & Fix Data")
            ai_date = parse_date(data.get("Date")) or date.today()
            txn_date = st.date_input("Entry Date", ai_date)
            mapping, codes_list = get_master_map()
            existing_names = list(mapping.keys())
            
            def prepare_df_with_code(raw_data, col_name, prefix):
                rows = []
                temp_codes = codes_list.copy()
                for r in raw_data:
                    raw_val = r.get("Party") or r.get("Supplier") or ""
                    raw_name = smart_match_party(raw_val, existing_names)
                    if raw_name in mapping and mapping[raw_name]:
                        code = mapping[raw_name]
                    else:
                        code = get_next_code(temp_codes, prefix)
                        temp_codes.append(code)
                    row = r.copy()
                    if col_name == "Supplier" and "Party" in row: del row["Party"]
                    row[col_name] = raw_name
                    row["Code"] = code
                    rows.append(row)
                return pd.DataFrame(rows)

            st.markdown("#### 1. Sales (Retailers - R)")
            raw_s = data.get("CustomerDues", [])
            df_s = prepare_df_with_code(raw_s, "Party", "R") if raw_s else pd.DataFrame(columns=["Party", "Code", "Amount"])
            ed_s = st.data_editor(df_s, num_rows="dynamic", key="s_ed")
            
            st.markdown("#### 2. Payments (Retailers - R)")
            raw_p = data.get("PaymentsReceived", [])
            df_p = prepare_df_with_code(raw_p, "Party", "R") if raw_p else pd.DataFrame(columns=["Party", "Code", "Amount", "Mode"])
            ed_p = st.data_editor(df_p, num_rows="dynamic", key="p_ed")
            
            st.markdown("#### 3. Supplier Payments (Suppliers - S)")
            raw_su = data.get("PaymentsToSuppliers", [])
            df_su = prepare_df_with_code(raw_su, "Supplier", "S") if raw_su else pd.DataFrame(columns=["Supplier", "Code", "Amount", "Mode"])
            ed_su = st.data_editor(df_su, num_rows="dynamic", key="su_ed")
            
            st.markdown("#### 4. Purchases (Suppliers - S)")
            raw_g = data.get("GoodsReceived", [])
            df_g = prepare_df_with_code(raw_g, "Supplier", "S") if raw_g else pd.DataFrame(columns=["Supplier", "Code", "Items", "Amount"])
            ed_g = st.data_editor(df_g, num_rows="dynamic", key="g_ed")

            if st.form_submit_button("💾 Save All"):
                sh = get_sheet_object()
                dt = str(txn_date)
                master_updates = []
                seen_new_codes = set()
                
                for df in [ed_s, ed_p]:
                    for _, r in df.iterrows():
                        p, c = str(r["Party"]).strip(), str(r["Code"]).strip()
                        if p and c:
                            if (p not in mapping) or (p in mapping and not mapping[p]):
                                if c not in seen_new_codes:
                                    master_updates.append([p, c, "Customer", "", ""])
                                    seen_new_codes.add(c)
                                    mapping[p] = c
                
                for df, col in [(ed_su, "Supplier"), (ed_g, "Supplier")]:
                    for _, r in df.iterrows():
                        p, c = str(r[col]).strip(), str(r["Code"]).strip()
                        if p and c:
                            if (p not in mapping) or (p in mapping and not mapping[p]):
                                if c not in seen_new_codes:
                                    master_updates.append([p, c, "Supplier", "", ""])
                                    seen_new_codes.add(c)
                                    mapping[p] = c

                if master_updates:
                    sh.worksheet("Party_Master").append_rows(master_updates)
                    st.toast(f"✅ Added {len(master_updates)} new parties/codes to Master List")

                rows = [[dt, r["Party"], clean_amount(r["Amount"])] for _, r in ed_s.iterrows() if r["Party"]]
                if rows: sh.worksheet("CustomerDues").append_rows(rows)
                rows = [[dt, r["Party"], clean_amount(r["Amount"]), r.get("Mode", "Cash")] for _, r in ed_p.iterrows() if r["Party"]]
                if rows: sh.worksheet("PaymentsReceived").append_rows(rows)
                rows = [[dt, r["Supplier"], clean_amount(r["Amount"]), r.get("Mode", "Cash")] for _, r in ed_su.iterrows() if r["Supplier"]]
                if rows: sh.worksheet("PaymentsToSuppliers").append_rows(rows)
                rows = [[dt, r["Supplier"], r.get("Items", ""), clean_amount(r["Amount"])] for _, r in ed_g.iterrows() if r["Supplier"]]
                if rows: sh.worksheet("GoodsReceived").append_rows(rows)
                st.success("Saved Successfully!"); del st.session_state['daily_data']; st.cache_data.clear()

# --- MAIN EXECUTION ---
# 1. Show Splash
show_splash_screen()

# 2. PASSWORD CHECK REMOVED
# (The code proceeds directly to the app below)

# 3. Router
if 'page' not in st.session_state: st.session_state['page'] = 'home'

if st.session_state['page'] == 'home': screen_home()
elif st.session_state['page'] == 'manual': screen_manual()
elif st.session_state['page'] == 'day_book': screen_day_book()
elif st.session_state['page'] == 'ledger': screen_ledger()
elif st.session_state['page'] == 'scan_historical': screen_digitize_ledger()
elif st.session_state['page'] == 'scan_daily': screen_scan_daily()
elif st.session_state['page'] == 'tools': screen_tools()
elif st.session_state['page'] == 'reminders': screen_reminders()
