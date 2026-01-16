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

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 5rem; }
    .total-dues-banner {
        background-color: #aa0000;
        color: white;
        padding: 15px;
        text-align: center;
        border-radius: 12px;
        font-size: 20px;
        font-weight: bold;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.2);
    }
    .stButton>button {
        width: 100%;
        height: 5em;
        border-radius: 12px;
        border: 1px solid #ddd;
        background-color: #ffffff;
        color: #333;
        font-weight: 600;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        transition: all 0.2s;
    }
    .stButton>button:hover {
        border-color: #aa0000;
        color: #aa0000;
        background-color: #fff5f5;
        transform: translateY(-2px);
    }
    /* Small buttons for internal tools */
    .small-btn > button { height: 3em !important; }
    </style>
""", unsafe_allow_html=True)

# --- 1. CONNECTION & UTILS ---
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

def get_all_party_names():
    names = set()
    # 1. Get from Transactions
    for sheet in ["CustomerDues", "PaymentsReceived"]:
        df = fetch_sheet_data(sheet)
        if not df.empty and "Party" in df.columns:
            names.update(df["Party"].astype(str).unique())
            
    # 2. Get from Master (in case some have no transactions yet)
    master = fetch_sheet_data("Party_Master")
    if not master.empty: names.update(master["Name"].astype(str).unique())
    
    return sorted([n.strip() for n in list(names) if n.strip()])

def clean_amount(val):
    try: return float(str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip())
    except: return 0.0

def parse_date(date_str):
    try: return pd.to_datetime(date_str).date()
    except: return None

def smart_match_party(scanned_name):
    parties = get_all_party_names()
    matches = difflib.get_close_matches(scanned_name, parties, n=1, cutoff=0.6)
    return matches[0] if matches else scanned_name

# --- 2. AI HELPERS ---
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
        2. Find Opening Balance (B/F) or Back Dues.
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
        prompt = """Analyze daily journal page. Map sections to: CustomerDues, PaymentsReceived, GoodsReceived, PaymentsToSuppliers.
        Return JSON: { "Date": "YYYY-MM-DD", 
        "CustomerDues": [{"Party": "Name", "Amount": 0}], 
        "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], 
        "GoodsReceived": [{"Supplier": "Name", "Items": "Desc", "Amount": 0}], 
        "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}] }"""
        response = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}])
        return extract_json_from_text(response.choices[0].message.content)
    except: return None

# --- 3. PDF ---
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

# --- 4. NAV HELPER ---
def go_to(page):
    st.session_state['page'] = page
    st.rerun()

# --- 5. SCREENS ---

def screen_home():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    total_sales = dues["Amount"].apply(clean_amount).sum() if not dues.empty else 0
    total_pymt = pymt["Amount"].apply(clean_amount).sum() if not pymt.empty else 0
    market_outstanding = total_sales - total_pymt
    
    st.markdown(f"""<div class="total-dues-banner">Total Dues: ₹ {market_outstanding:,.0f} ℹ️</div>""", unsafe_allow_html=True)
    
    c1, c2, c3 = st.columns(3)
    if c1.button("📝\nEntry"): go_to('manual')
    if c2.button("📒\nLedger"): go_to('ledger')
    if c3.button("🔔\nRemind"): go_to('reminders') # NEW REMINDERS
        
    c4, c5, c6 = st.columns(3)
    if c4.button("📸\nScan"): go_to('scan_daily')
    if c5.button("⚙️\nTools"): go_to('tools')
    if c6.button("📊\nReports"): st.toast("Coming Soon")

    st.markdown("---")
    st.markdown("##### 📅 Today's Summary")
    today = str(date.today())
    
    def get_sum(df, d_str):
        if df.empty or "Date" not in df.columns: return 0
        return df[df['Date'].astype(str) == d_str]["Amount"].apply(clean_amount).sum()

    sale_today = get_sum(dues, today)
    pymt_today = get_sum(pymt, today)
    
    col_a, col_b = st.columns(2)
    col_a.metric("Today's Sales", f"₹{sale_today:,.0f}")
    col_b.metric("Today's Collection", f"₹{pymt_today:,.0f}")

def screen_reminders():
    st.markdown("### 🔔 Payment Reminders")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    with st.spinner("Analyzing all accounts..."):
        # 1. Calculate Balances
        dues = fetch_sheet_data("CustomerDues")
        pymt = fetch_sheet_data("PaymentsReceived")
        
        # Group sales
        sales_map = {}
        if not dues.empty:
            sales_map = dues.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum()).to_dict()
            
        # Group payments
        pymt_map = {}
        if not pymt.empty:
            pymt_map = pymt.groupby("Party")["Amount"].apply(lambda x: x.apply(clean_amount).sum()).to_dict()
            
        # 2. Get Phones
        master = fetch_sheet_data("Party_Master")
        phone_map = {}
        if not master.empty:
            for _, r in master.iterrows():
                if str(r.get("Phone", "")).strip():
                    phone_map[r["Name"]] = str(r["Phone"]).strip()

        # 3. Build List
        all_parties = set(list(sales_map.keys()) + list(pymt_map.keys()))
        debtors = []
        for p in all_parties:
            bal = sales_map.get(p, 0) - pymt_map.get(p, 0)
            if bal > 100: # Only show if owing > 100
                debtors.append({
                    "Party": p,
                    "Balance": bal,
                    "Phone": phone_map.get(p, "")
                })
        
        debtors.sort(key=lambda x: x["Balance"], reverse=True)
        
    st.write(f"**{len(debtors)} Parties** owe you money.")
    
    for d in debtors:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            c1.write(f"**{d['Party']}**")
            c1.caption(f"Phone: {d['Phone'] if d['Phone'] else 'Not Saved'}")
            c1.write(f"🔴 ₹{d['Balance']:,.0f}")
            
            # WhatsApp Logic
            msg = f"Hello {d['Party']}, your pending balance with Gautam Pharma is Rs. {d['Balance']:,.0f}. Please pay at the earliest."
            encoded_msg = urllib.parse.quote(msg)
            
            if d['Phone']:
                # Clean phone number (remove spaces, ensure 91)
                clean_ph = re.sub(r'\D', '', d['Phone'])
                if not clean_ph.startswith("91") and len(clean_ph) == 10: clean_ph = "91" + clean_ph
                link = f"https://wa.me/{clean_ph}?text={encoded_msg}"
            else:
                # Generic link (user selects contact)
                link = f"https://wa.me/?text={encoded_msg}"
                
            c2.link_button("💬 Send", link)

def screen_tools():
    st.markdown("### ⚙️ Admin Tools")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔄 Merge", "✏️ Edit Txn", "📇 Party Info", "🧨 Reset"])
    
    with tab1:
        st.write("Combine two party names.")
        parties = get_all_party_names()
        c1, c2 = st.columns(2)
        old = c1.selectbox("From (Wrong)", parties, index=None, placeholder="Search...")
        new = c2.selectbox("To (Correct)", parties, index=None, placeholder="Search...")
        if st.button("Merge") and old and new:
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
                            if i>0 and r[col] == old:
                                ups.append({"range": f"{chr(65+col)}{i+1}", "values": [[new]]})
                                count += 1
                        if ups: ws.batch_update(ups)
                except: pass
            st.success(f"Merged {count} entries!")
            st.cache_data.clear()

    with tab2:
        st.write("Find and Edit specific transactions.")
        sheet_choice = st.selectbox("Sheet", ["CustomerDues", "PaymentsReceived", "GoodsReceived"])
        f_party = st.selectbox("Filter by Party (Optional)", ["All"] + get_all_party_names())
        c1, c2 = st.columns(2)
        f_start = c1.date_input("Start Date", date.today().replace(day=1))
        f_end = c2.date_input("End Date", date.today())
        
        if st.button("🔎 Load Transactions"):
            df = fetch_sheet_data(sheet_choice)
            if not df.empty:
                if f_party != "All":
                    col = "Party" if "Party" in df.columns else "Supplier"
                    if col in df.columns: df = df[df[col] == f_party]
                if "Date" in df.columns:
                    df["DateObj"] = pd.to_datetime(df["Date"], errors='coerce').dt.date
                    df = df[(df["DateObj"] >= f_start) & (df["DateObj"] <= f_end)].drop(columns=["DateObj"])
                st.session_state['edit_df'] = df
                st.session_state['edit_sheet'] = sheet_choice
            
        if 'edit_df' in st.session_state:
            edited = st.data_editor(st.session_state['edit_df'], num_rows="dynamic", use_container_width=True)
            if st.button("💾 Save Changes"):
                try:
                    if f_party == "All":
                        sh = get_sheet_object()
                        ws = sh.worksheet(st.session_state['edit_sheet'])
                        ws.clear()
                        ws.update([edited.columns.tolist()] + edited.astype(str).values.tolist())
                        st.success("Sheet Updated!")
                        st.cache_data.clear()
                    else: st.warning("To save, please filter by 'All' first (Safety Lock).")
                except Exception as e: st.error(str(e))

    with tab3:
        st.write("Edit Party Names & Details.")
        
        # 1. Fetch Existing Master Data
        df_master = fetch_sheet_data("Party_Master")
        
        # 2. Fetch All Names from Ledger
        all_ledger_names = get_all_party_names()
        
        # 3. Merge: Add missing names to Master (in memory)
        if not df_master.empty:
            existing_names = df_master["Name"].astype(str).tolist()
        else:
            existing_names = []
            df_master = pd.DataFrame(columns=["Name", "Type", "Phone", "Address"])
            
        new_rows = []
        for name in all_ledger_names:
            if name not in existing_names:
                new_rows.append({"Name": name, "Type": "Customer", "Phone": "", "Address": ""})
        
        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df_master = pd.concat([df_master, df_new], ignore_index=True)
            
        # 4. Show Editor
        edited_master = st.data_editor(df_master, num_rows="dynamic", use_container_width=True)
        
        if st.button("Save Party Master"):
            try:
                sh = get_sheet_object()
                ws = sh.worksheet("Party_Master")
                ws.clear()
                ws.update([edited_master.columns.tolist()] + edited_master.astype(str).values.tolist())
                st.success("Saved!")
                st.cache_data.clear()
            except Exception as e: st.error(str(e))

    with tab4:
        st.error("⚠️ **FACTORY RESET**")
        confirm_text = st.text_input("Type WIPE DATA to confirm:")
        if st.button("🧨 Delete All", type="primary", disabled=(confirm_text != "WIPE DATA")):
            sh = get_sheet_object()
            sheets = {"CustomerDues": ["Date","Party","Amount"], "PaymentsReceived": ["Date","Party","Amount","Mode"], 
                      "PaymentsToSuppliers": ["Date","Supplier","Amount","Mode"], "GoodsReceived": ["Date","Supplier","Items","Amount"],
                      "Party_Master": ["Name","Type","Phone","Address"]}
            for s, h in sheets.items():
                try: ws = sh.worksheet(s); ws.clear(); ws.update(range_name="A1", values=[h])
                except: pass
            st.success("Reset Complete!"); st.cache_data.clear(); time.sleep(2); st.rerun()

def screen_digitize_ledger():
    st.markdown("### 📂 Digitize Old Ledger")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    img = st.file_uploader("Upload Image", type=['jpg', 'png', 'jpeg'])
    if img and st.button("🚀 Process"):
        with st.spinner("Analyzing..."):
            data = extract_single_party_ledger(img.read())
            if data: st.session_state['hist_data'] = data; st.rerun()
            
    if 'hist_data' in st.session_state:
        data = st.session_state['hist_data']
        with st.form("save_hist"):
            scanned = data.get("PartyName", "")
            final_name = smart_match_party(scanned)
            st.text_input("Party Name", value=final_name, disabled=True)
            
            c1, c2 = st.columns(2)
            op_bal = c1.number_input("Opening Balance", value=float(data.get("OpeningBalance", 0)))
            op_date = c2.date_input("Opening Bal Date", date.today().replace(month=4, day=1))
            
            df = pd.DataFrame(data.get("Transactions", []))
            for c in ["Date", "Particulars", "Debit", "Credit"]: 
                if c not in df.columns: df[c] = ""
            edited_df = st.data_editor(df, num_rows="dynamic")
            
            if st.form_submit_button("Save"):
                sh = get_sheet_object()
                s_rows, p_rows = [], []
                if op_bal > 0: s_rows.append([str(op_date), final_name, op_bal])
                for _, r in edited_df.iterrows():
                    d = r.get("Date", str(date.today()))
                    dr, cr = clean_amount(r.get("Debit", 0)), clean_amount(r.get("Credit", 0))
                    if dr > 0: s_rows.append([d, final_name, dr])
                    if cr > 0: p_rows.append([d, final_name, cr, "Old Ledger"])
                if s_rows: sh.worksheet("CustomerDues").append_rows(s_rows)
                if p_rows: sh.worksheet("PaymentsReceived").append_rows(p_rows)
                st.success("Saved!"); st.cache_data.clear(); del st.session_state['hist_data']

def screen_manual():
    st.markdown("### 📝 New Entry")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    parties = get_all_party_names()
    with st.form("manual"):
        c1, c2 = st.columns(2)
        d_val = c1.date_input("Date", date.today())
        e_type = c2.selectbox("Type", ["Sale (Bill)", "Payment Received", "Supplier Payment", "Purchase (Goods)"])
        
        c3, c4 = st.columns(2)
        p_in = c3.selectbox("Party / Supplier", ["Add New"] + parties, index=None, placeholder="Search...")
        if p_in == "Add New": party = c3.text_input("New Name")
        else: party = p_in
        amt = c4.number_input("Amount", min_value=0.0)
        
        extra = ""
        if e_type in ["Payment Received", "Supplier Payment"]: extra = st.selectbox("Mode", ["Cash", "UPI", "Cheque"])
        elif e_type == "Purchase (Goods)": extra = st.text_input("Items", "Goods")

        if st.form_submit_button("Save"):
            sh = get_sheet_object()
            if not sh or not party or amt == 0: st.error("Invalid Input"); st.stop()
            try:
                if e_type == "Sale (Bill)": sh.worksheet("CustomerDues").append_row([str(d_val), party, amt])
                elif e_type == "Payment Received": sh.worksheet("PaymentsReceived").append_row([str(d_val), party, amt, extra])
                elif e_type == "Supplier Payment": sh.worksheet("PaymentsToSuppliers").append_row([str(d_val), party, amt, extra])
                elif e_type == "Purchase (Goods)": sh.worksheet("GoodsReceived").append_row([str(d_val), party, extra, amt])
                st.success("Saved!"); st.cache_data.clear()
            except Exception as e: st.error(str(e))

def screen_ledger():
    st.markdown("### 📒 Party Ledger")
    if st.button("🏠 Home", use_container_width=True): go_to('home')
    sel_party = st.selectbox("Party", get_all_party_names(), index=None, placeholder="Search...")
    
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
        
        ledger = []
        if not d_df.empty:
            sub = d_df[d_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e:
                    ledger.append({"Date": r_date, "Desc": "Sale", "Dr": clean_amount(r['Amount']), "Cr": 0})
        if not p_df.empty:
            sub = p_df[p_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                r_date = parse_date(str(r['Date']))
                if r_date and s <= r_date <= e:
                    ledger.append({"Date": r_date, "Desc": f"Rx ({r.get('Mode','')})", "Dr": 0, "Cr": clean_amount(r['Amount'])})
        
        if ledger:
            df = pd.DataFrame(ledger).sort_values('Date')
            df.columns = ["Date", "Description", "Debit", "Credit"]
            bal = df['Debit'].sum() - df['Credit'].sum()
            st.dataframe(df, use_container_width=True)
            st.metric("Net Balance", f"₹{abs(bal):,.2f}", "Receivable" if bal>0 else "Payable")
            msg = f"Hello {sel_party}, Balance: {bal}"
            st.link_button("💬 WhatsApp", f"https://wa.me/?text={urllib.parse.quote(msg)}", use_container_width=True)
            pdf_bytes = generate_pdf(sel_party, df, s, e)
            st.download_button("📄 PDF", pdf_bytes, "stmt.pdf", "application/pdf", use_container_width=True)
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
            txn_date = st.date_input("Entry Date", date.today())
            
            st.markdown("#### 1. Sales (Customer Dues)")
            raw_sales = data.get("CustomerDues", [])
            for r in raw_sales:
                if "Party" in r: r["Party"] = smart_match_party(r["Party"])
            df_sales = pd.DataFrame(raw_sales) if raw_sales else pd.DataFrame(columns=["Party", "Amount"])
            edited_sales = st.data_editor(df_sales, num_rows="dynamic", key="s_ed")
            
            st.markdown("#### 2. Payments Received")
            raw_pymt = data.get("PaymentsReceived", [])
            for r in raw_pymt:
                if "Party" in r: r["Party"] = smart_match_party(r["Party"])
            df_pymt = pd.DataFrame(raw_pymt) if raw_pymt else pd.DataFrame(columns=["Party", "Amount", "Mode"])
            edited_pymt = st.data_editor(df_pymt, num_rows="dynamic", key="p_ed")
            
            st.markdown("#### 3. Supplier Payments")
            raw_supp = data.get("PaymentsToSuppliers", [])
            df_supp = pd.DataFrame(raw_supp) if raw_supp else pd.DataFrame(columns=["Supplier", "Amount", "Mode"])
            edited_supp = st.data_editor(df_supp, num_rows="dynamic", key="su_ed")
            
            st.markdown("#### 4. Purchases (Goods)")
            raw_goods = data.get("GoodsReceived", [])
            df_goods = pd.DataFrame(raw_goods) if raw_goods else pd.DataFrame(columns=["Supplier", "Items", "Amount"])
            edited_goods = st.data_editor(df_goods, num_rows="dynamic", key="g_ed")

            if st.form_submit_button("💾 Save All"):
                sh = get_sheet_object()
                dt = str(txn_date)
                
                rows = [[dt, r.get("Party"), clean_amount(r.get("Amount"))] for _, r in edited_sales.iterrows() if r.get("Party")]
                if rows: sh.worksheet("CustomerDues").append_rows(rows)
                
                rows = [[dt, r.get("Party"), clean_amount(r.get("Amount")), r.get("Mode", "Cash")] for _, r in edited_pymt.iterrows() if r.get("Party")]
                if rows: sh.worksheet("PaymentsReceived").append_rows(rows)
                
                rows = [[dt, r.get("Supplier"), clean_amount(r.get("Amount")), r.get("Mode", "Cash")] for _, r in edited_supp.iterrows() if r.get("Supplier")]
                if rows: sh.worksheet("PaymentsToSuppliers").append_rows(rows)
                
                rows = [[dt, r.get("Supplier"), r.get("Items", ""), clean_amount(r.get("Amount"))] for _, r in edited_goods.iterrows() if r.get("Supplier")]
                if rows: sh.worksheet("GoodsReceived").append_rows(rows)

                st.success("Saved Successfully!")
                del st.session_state['daily_data']
                st.cache_data.clear()

# --- MAIN ---
if 'page' not in st.session_state: st.session_state['page'] = 'home'

if st.session_state['page'] == 'home': screen_home()
elif st.session_state['page'] == 'manual': screen_manual()
elif st.session_state['page'] == 'ledger': screen_ledger()
elif st.session_state['page'] == 'scan_historical': screen_digitize_ledger()
elif st.session_state['page'] == 'scan_daily': screen_scan_daily()
elif st.session_state['page'] == 'tools': screen_tools()
elif st.session_state['page'] == 'reminders': screen_reminders()
