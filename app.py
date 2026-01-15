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
    /* Danger Zone Button Red */
    .danger-btn > button {
        border-color: #ff4b4b;
        color: #ff4b4b;
    }
    .danger-btn > button:hover {
        background-color: #ff4b4b;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

# --- 1. CONNECTION & AUTO-REPAIR ---
@st.cache_resource
def get_gsheet_client():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        credentials = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        return gspread.authorize(credentials)
    except Exception as e: return None

def check_and_fix_headers():
    client = get_gsheet_client()
    if not client: return
    try:
        sh = client.open("Gautam_Pharma_Ledger")
        required_headers = {
            "CustomerDues": ["Date", "Party", "Amount"],
            "PaymentsReceived": ["Date", "Party", "Amount", "Mode"],
            "PaymentsToSuppliers": ["Date", "Supplier", "Amount", "Mode"],
            "GoodsReceived": ["Date", "Supplier", "Items", "Amount"],
            "Party_Master": ["Name", "Type", "Phone", "Address"]
        }
        for sheet_name, headers in required_headers.items():
            try:
                ws = sh.worksheet(sheet_name)
            except:
                ws = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
            
            existing = ws.row_values(1)
            if not existing:
                ws.insert_row(headers, 1)
    except: pass

check_and_fix_headers()

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
    master = fetch_sheet_data("Party_Master")
    if not master.empty: names.update(master["Name"].astype(str).unique())
    
    for sheet in ["CustomerDues", "PaymentsReceived"]:
        df = fetch_sheet_data(sheet)
        if not df.empty and "Party" in df.columns:
            names.update(df["Party"].astype(str).unique())
    return sorted([n.strip() for n in list(names) if n.strip()])

def clean_amount(val):
    try: return float(str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip())
    except: return 0.0

# --- 2. LOGIC HELPERS ---
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
        prompt = """Analyze daily journal. Map to: CustomerDues, PaymentsReceived, GoodsReceived, PaymentsToSuppliers.
        Return JSON: { "Date": "YYYY-MM-DD", "CustomerDues": [{"Party": "Name", "Amount": 0}], "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], "GoodsReceived": [{"Supplier": "Name", "Amount": 0}], "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0}] }"""
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

# --- 4. SCREENS ---

def screen_home():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    total_sales = dues["Amount"].apply(clean_amount).sum() if not dues.empty else 0
    total_pymt = pymt["Amount"].apply(clean_amount).sum() if not pymt.empty else 0
    market_outstanding = total_sales - total_pymt
    
    st.markdown(f"""<div class="total-dues-banner">Total Dues: ₹ {market_outstanding:,.0f} ℹ️</div>""", unsafe_allow_html=True)
    
    # GRID MENU
    c1, c2, c3 = st.columns(3)
    with c1: 
        if st.button("📝\nEntry"): st.session_state['page'] = 'manual'
    with c2: 
        if st.button("📒\nLedger"): st.session_state['page'] = 'ledger'
    with c3: 
        if st.button("📸\nScan"): st.session_state['page'] = 'scan_daily'
        
    c4, c5, c6 = st.columns(3)
    with c4: 
        if st.button("📂\nOld Dues"): st.session_state['page'] = 'scan_historical'
    with c5: 
        if st.button("⚙️\nTools"): st.session_state['page'] = 'tools'
    with c6: 
        if st.button("📊\nReports"): st.toast("Coming Soon")

    st.markdown("---")
    st.markdown("##### 📅 Quick Summary")
    today = str(date.today())
    
    def get_sum(df, d_str):
        if df.empty or "Date" not in df.columns: return 0
        return df[df['Date'].astype(str) == d_str]["Amount"].apply(clean_amount).sum()

    sale_today = get_sum(dues, today)
    pymt_today = get_sum(pymt, today)
    
    col_a, col_b = st.columns(2)
    col_a.metric("Today's Sales", f"₹{sale_today:,.0f}")
    col_b.metric("Today's Collection", f"₹{pymt_today:,.0f}")

def screen_tools():
    st.markdown("### ⚙️ Admin Tools")
    if st.button("🏠 Home", use_container_width=True): st.session_state['page'] = 'home'; st.rerun()
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔄 Merge Parties", "✏️ Edit Transactions", "📇 Party Details", "🧨 Danger Zone"])
    
    # 1. MERGE PARTIES
    with tab1:
        st.write("Combine two party names into one.")
        parties = get_all_party_names()
        c1, c2 = st.columns(2)
        # UPDATED: No default "Select..."
        old_name = c1.selectbox("Wrong Name (From)", parties, index=None, placeholder="Search name...")
        new_name = c2.selectbox("Correct Name (To)", parties, index=None, placeholder="Search name...")
        
        if st.button("Merge Now") and old_name and new_name:
            sh = get_sheet_object()
            count = 0
            for s_name in ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"]:
                try:
                    ws = sh.worksheet(s_name)
                    all_vals = ws.get_all_values()
                    header = all_vals[0]
                    col_idx = -1
                    if "Party" in header: col_idx = header.index("Party")
                    elif "Supplier" in header: col_idx = header.index("Supplier")
                    
                    if col_idx != -1:
                        updates = []
                        for i, row in enumerate(all_vals):
                            if i == 0: continue 
                            if row[col_idx] == old_name:
                                updates.append({"range": f"{chr(65+col_idx)}{i+1}", "values": [[new_name]]})
                                count += 1
                        if updates: ws.batch_update(updates)
                except: pass
            st.success(f"Merged {count} records from '{old_name}' to '{new_name}'!")
            st.cache_data.clear()

    # 2. EDIT TRANSACTIONS
    with tab2:
        st.write("Directly edit raw data sheets.")
        sheet_choice = st.selectbox("Select Sheet", ["CustomerDues", "PaymentsReceived", "GoodsReceived"])
        
        if st.button("Load Data"):
            st.session_state['edit_df'] = fetch_sheet_data(sheet_choice)
            
        if 'edit_df' in st.session_state:
            edited = st.data_editor(st.session_state['edit_df'], num_rows="dynamic")
            if st.button("💾 Update Google Sheet"):
                try:
                    sh = get_sheet_object()
                    ws = sh.worksheet(sheet_choice)
                    ws.clear()
                    data_list = [edited.columns.tolist()] + edited.astype(str).values.tolist()
                    ws.update(data_list)
                    st.success("Updated Successfully!")
                    st.cache_data.clear()
                except Exception as e: st.error(f"Error: {e}")

    # 3. PARTY DETAILS
    with tab3:
        st.write("Manage Phone Numbers & Addresses.")
        df_master = fetch_sheet_data("Party_Master")
        edited_master = st.data_editor(df_master, num_rows="dynamic")
        
        if st.button("Save Party Details"):
            try:
                sh = get_sheet_object()
                ws = sh.worksheet("Party_Master")
                ws.clear()
                data_list = [edited_master.columns.tolist()] + edited_master.astype(str).values.tolist()
                ws.update(data_list)
                st.success("Master Data Saved!")
            except Exception as e: st.error(f"Error: {e}")

    # 4. DANGER ZONE (RESET)
    with tab4:
        st.error("⚠️ **FACTORY RESET**")
        st.write("This will delete ALL transactions and parties to start fresh for your business.")
        st.warning("To confirm, please type **WIPE DATA** (all caps) in the box below.")
        
        confirm_text = st.text_input("Type confirmation code here:")
        
        # Button is disabled unless text matches exactly
        if st.button("🧨 Delete All Data", type="primary", disabled=(confirm_text != "WIPE DATA")):
            sh = get_sheet_object()
            sheets_to_clean = {
                "CustomerDues": ["Date", "Party", "Amount"],
                "PaymentsReceived": ["Date", "Party", "Amount", "Mode"],
                "PaymentsToSuppliers": ["Date", "Supplier", "Amount", "Mode"],
                "GoodsReceived": ["Date", "Supplier", "Items", "Amount"],
                "Party_Master": ["Name", "Type", "Phone", "Address"]
            }
            
            progress = st.progress(0)
            for i, (s_name, headers) in enumerate(sheets_to_clean.items()):
                try:
                    ws = sh.worksheet(s_name)
                    ws.clear()
                    ws.update(range_name="A1", values=[headers])
                except: pass
                progress.progress((i + 1) / len(sheets_to_clean))
            
            st.success("✅ App Reset Successfully! You can now start fresh.")
            st.cache_data.clear()
            time.sleep(2)
            st.rerun()

def screen_digitize_ledger():
    st.markdown("### 📂 Digitize Old Ledger")
    if st.button("🏠 Home", use_container_width=True): st.session_state['page'] = 'home'; st.rerun()
    st.info("Extracts Party Name, Opening Balance & Table.")
    
    img = st.file_uploader("Upload Image", type=['jpg', 'png', 'jpeg'])
    if img and st.button("🚀 Process"):
        with st.spinner("Analyzing..."):
            data = extract_single_party_ledger(img.read())
            if data:
                st.session_state['hist_data'] = data
                st.rerun()
            else: st.error("Failed to read image.")
            
    if 'hist_data' in st.session_state:
        data = st.session_state['hist_data']
        with st.form("save_hist"):
            scanned = data.get("PartyName", "")
            parties = get_all_party_names()
            matches = difflib.get_close_matches(scanned, parties, n=1, cutoff=0.6)
            
            final_name = scanned
            if matches:
                st.warning(f"Similiar party found: {matches[0]}")
                use_exist = st.checkbox(f"Use '{matches[0]}'?", value=True)
                if use_exist: final_name = matches[0]
            
            st.text_input("Party Name", value=final_name, disabled=True)
            op_bal = st.number_input("Opening Balance", value=float(data.get("OpeningBalance", 0)))
            
            df = pd.DataFrame(data.get("Transactions", []))
            for c in ["Date", "Particulars", "Debit", "Credit"]: 
                if c not in df.columns: df[c] = ""
            edited_df = st.data_editor(df, num_rows="dynamic")
            
            if st.form_submit_button("Save"):
                sh = get_sheet_object()
                s_rows, p_rows = [], []
                
                if op_bal > 0: s_rows.append([str(date.today()), final_name, op_bal])
                
                for _, r in edited_df.iterrows():
                    d = r.get("Date", str(date.today()))
                    dr = clean_amount(r.get("Debit", 0))
                    cr = clean_amount(r.get("Credit", 0))
                    if dr > 0: s_rows.append([d, final_name, dr])
                    if cr > 0: p_rows.append([d, final_name, cr, "Old Ledger"])
                
                if s_rows: sh.worksheet("CustomerDues").append_rows(s_rows)
                if p_rows: sh.worksheet("PaymentsReceived").append_rows(p_rows)
                st.success("Saved!")
                st.cache_data.clear()
                del st.session_state['hist_data']

def screen_manual():
    st.markdown("### 📝 New Entry")
    if st.button("🏠 Home", use_container_width=True): st.session_state['page'] = 'home'; st.rerun()
    
    all_parties = get_all_party_names()
    with st.form("manual"):
        c1, c2 = st.columns(2)
        d_val = c1.date_input("Date", date.today())
        e_type = c2.selectbox("Type", ["Sale (Bill)", "Payment Received", "Supplier Payment", "Purchase (Goods)"])
        
        c3, c4 = st.columns(2)
        # UPDATED: No default "Select..."
        p_in = c3.selectbox("Party / Supplier", ["Add New"] + all_parties, index=None, placeholder="Search Party...")
        
        if p_in == "Add New": party = c3.text_input("New Name")
        else: party = p_in
        amt = c4.number_input("Amount", min_value=0.0)
        
        extra = ""
        if e_type in ["Payment Received", "Supplier Payment"]:
            extra = st.selectbox("Mode", ["Cash", "UPI", "Cheque"])
        elif e_type == "Purchase (Goods)":
            extra = st.text_input("Items", "Goods")

        if st.form_submit_button("Save"):
            sh = get_sheet_object()
            if not sh or not party or amt == 0: st.error("Invalid Input"); st.stop()
            
            try:
                if e_type == "Sale (Bill)": sh.worksheet("CustomerDues").append_row([str(d_val), party, amt])
                elif e_type == "Payment Received": sh.worksheet("PaymentsReceived").append_row([str(d_val), party, amt, extra])
                elif e_type == "Supplier Payment": sh.worksheet("PaymentsToSuppliers").append_row([str(d_val), party, amt, extra])
                elif e_type == "Purchase (Goods)": sh.worksheet("GoodsReceived").append_row([str(d_val), party, extra, amt])
                st.success("Saved!")
                st.cache_data.clear()
            except Exception as e: st.error(str(e))

def screen_ledger():
    st.markdown("### 📒 Party Ledger")
    if st.button("🏠 Home", use_container_width=True): st.session_state['page'] = 'home'; st.rerun()
    
    # UPDATED: No default "Select..."
    sel_party = st.selectbox("Party", get_all_party_names(), index=None, placeholder="Search Party...")
    
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
    
    if st.button("🔎 Show") and sel_party:
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        
        ledger = []
        if not d_df.empty:
            sub = d_df[d_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                if s <= pd.to_datetime(r['Date']).date() <= e:
                    ledger.append({"Date": r['Date'], "Desc": "Sale", "Dr": clean_amount(r['Amount']), "Cr": 0})
        if not p_df.empty:
            sub = p_df[p_df['Party'].astype(str) == sel_party]
            for _, r in sub.iterrows():
                if s <= pd.to_datetime(r['Date']).date() <= e:
                    ledger.append({"Date": r['Date'], "Desc": f"Rx ({r.get('Mode','')})", "Dr": 0, "Cr": clean_amount(r['Amount'])})
        
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
        else: st.info("No Data")

def screen_scan_daily():
    st.markdown("### 📸 Daily Scan")
    if st.button("🏠 Home", use_container_width=True): st.session_state['page'] = 'home'; st.rerun()
    img = st.file_uploader("Journal Page", type=['jpg', 'png'])
    
    if img and st.button("Extract"):
        with st.spinner("AI Reading..."):
            data = run_daily_scan_extraction(img.read())
            if data: st.session_state['daily_data'] = data; st.rerun()
            
    if 'daily_data' in st.session_state:
        st.json(st.session_state['daily_data'])
        if st.button("Save All"):
            sh = get_sheet_object()
            d = st.session_state['daily_data']
            dt = d.get("Date", str(date.today()))
            
            for k, sheet in {"CustomerDues":"CustomerDues", "PaymentsReceived":"PaymentsReceived"}.items():
                 rows = [[dt, r["Party"], r["Amount"], r.get("Mode","")] for r in d.get(k, []) if "Party" in r]
                 if rows: sh.worksheet(sheet).append_rows(rows)

            st.success("Saved!")
            del st.session_state['daily_data']

# --- MAIN ---
if 'page' not in st.session_state: st.session_state['page'] = 'home'

if st.session_state['page'] == 'home': screen_home()
elif st.session_state['page'] == 'manual': screen_manual()
elif st.session_state['page'] == 'ledger': screen_ledger()
elif st.session_state['page'] == 'scan_historical': screen_digitize_ledger()
elif st.session_state['page'] == 'scan_daily': screen_scan_daily()
elif st.session_state['page'] == 'tools': screen_tools()
