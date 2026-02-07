import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from openai import OpenAI
from datetime import date, datetime, timedelta
import json
from fpdf import FPDF
import base64
import difflib
import urllib.parse
import time
import re
from PIL import Image
import io
import traceback

# --- SAFETY IMPORT FOR VOICE ---
try:
    from streamlit_mic_recorder import mic_recorder
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma", layout="centered", page_icon="💊")

# --- CUSTOM CSS: KHATABOOK THEME ---
st.markdown("""
    <style>
    /* 1. Main Layout & Colors (White/Blue) */
    .stApp { background-color: #f7f8fa; color: #1c1c1c; font-family: 'Roboto', sans-serif; }
    
    /* 2. Header Style */
    h1, h2, h3 { color: #2c3e50; font-weight: 700; }
    
    /* 3. Cards (Party List) */
    .party-card {
        background-color: white;
        padding: 15px;
        border-radius: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        margin-bottom: 10px;
        border: 1px solid #e0e0e0;
    }
    
    /* 4. Balance Colors */
    .bal-green { color: #00c853; font-weight: 700; font-size: 16px; text-align: right; }
    .bal-red { color: #d50000; font-weight: 700; font-size: 16px; text-align: right; }
    .sub-text { font-size: 12px; color: #757575; text-align: right; }
    .party-name { font-size: 16px; font-weight: 600; color: #333; }
    .date-text { font-size: 12px; color: #9e9e9e; }

    /* 5. Buttons */
    .stButton>button {
        border-radius: 25px; font-weight: 600; border: 1px solid #ddd;
        background-color: white; color: #333;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }
    .stButton>button:hover {
        border-color: #00c853; color: #00c853;
    }
    
    /* 6. Dashboard Cards */
    div[data-testid="metric-container"] {
        background-color: white; border: 1px solid #eee;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    </style>
""", unsafe_allow_html=True)

# --- 1. CONNECTORS & UTILS ---
@st.cache_resource
def get_credentials():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    except: return None

@st.cache_resource
def get_gsheet_client():
    creds = get_credentials()
    if creds: return gspread.authorize(creds)
    return None

@st.cache_resource
def get_drive_service():
    creds = get_credentials()
    if creds: return build('drive', 'v3', credentials=creds)
    return None

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
        data = sh.worksheet(sheet_name).get_all_values()
        if not data: return pd.DataFrame()
        
        headers = data.pop(0)
        df = pd.DataFrame(data, columns=headers)
        
        # Clean Data
        df.replace("", pd.NA, inplace=True)
        df.dropna(how='all', inplace=True)
        df.fillna("", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        
        # Fix Supplier Column Name Logic
        if sheet_name in ["PaymentsToSuppliers", "GoodsReceived"]:
            if "Party" in df.columns: df.rename(columns={"Party": "Supplier"}, inplace=True)
            
        # Strip Spaces
        for col in ["Party", "Supplier"]:
            if col in df.columns: df[col] = df[col].astype(str).str.strip()
            
        return df
    except: return pd.DataFrame()

# --- HELPER FUNCS ---
def clean_amount(val):
    try:
        val = str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip()
        return float(val) if val else 0.0
    except: return 0.0

def parse_date(date_str):
    if not date_str: return None
    try: return pd.to_datetime(date_str, dayfirst=True).date()
    except: 
        try: return pd.to_datetime(date_str).date()
        except: return None

def extract_name_display(display_str):
    if "(" in display_str and ")" in display_str: return display_str.split(" (")[0].strip()
    return display_str.strip()

def get_all_party_names_display():
    df = fetch_sheet_data("Party_Master")
    names = []
    if not df.empty:
        for _, r in df.iterrows():
            n = str(r.get("Name", "")).strip()
            if n: names.append(n)
    return sorted(list(set(names)))

def get_party_balances():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    supp = fetch_sheet_data("PaymentsToSuppliers")
    
    balances = {}
    last_dates = {}

    if not dues.empty:
        for _, r in dues.iterrows():
            p = r.get("Party"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) + amt
            last_dates[p] = r.get("Date")
    if not pymt.empty:
        for _, r in pymt.iterrows():
            p = r.get("Party"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) - amt
            last_dates[p] = r.get("Date")

    if not goods.empty:
        for _, r in goods.iterrows():
            p = r.get("Supplier"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) - amt # Payable
    if not supp.empty:
        for _, r in supp.iterrows():
            p = r.get("Supplier"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) + amt

    return balances, last_dates

# --- 2. AI & DRIVE HELPERS ---
def compress_image(image_file):
    img = Image.open(image_file)
    if img.mode in ("RGBA", "P"): img = img.convert("RGB")
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=65, optimize=True)
    output.seek(0)
    return output

def upload_to_drive(file_buffer, filename):
    try:
        service = get_drive_service()
        if not service: return None
        res = service.files().list(q="name='Gautam_Scans' and mimeType='application/vnd.google-apps.folder'").execute()
        if not res.get('files'):
            f = service.files().create(body={'name': 'Gautam_Scans', 'mimeType': 'application/vnd.google-apps.folder'}, fields='id').execute()
            fid = f.get('id')
        else: fid = res.get('files')[0].get('id')
        
        media = MediaIoBaseUpload(file_buffer, mimetype='image/jpeg', resumable=True)
        f = service.files().create(body={'name': filename, 'parents': [fid]}, media_body=media, fields='id, webViewLink').execute()
        service.permissions().create(fileId=f.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        return f.get('webViewLink')
    except: return None

def analyze_image_generic(prompt, image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        resp = client.chat.completions.create(model="gpt-4o", messages=[
            {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}
        ])
        s = resp.choices[0].message.content
        return json.loads(s[s.find('{'):s.rfind('}')+1])
    except: return None

# --- 3. SCREENS ---

def screen_home():
    st.markdown("### 💊 Gautam Pharma")
    
    bals, dates = get_party_balances()
    total_get = sum([v for v in bals.values() if v > 0])
    total_give = sum([abs(v) for v in bals.values() if v < 0])
    
    st.markdown(f"""
    <div style="background:white; padding:15px; border-radius:10px; border:1px solid #ddd; margin-bottom:15px; display:flex; justify-content:space-between;">
        <div style="text-align:center; width:48%; border-right:1px solid #eee;">
            <div style="color:#00c853; font-weight:bold; font-size:18px;">₹ {total_get:,.0f}</div>
            <div style="color:#757575; font-size:12px;">You'll Get</div>
        </div>
        <div style="text-align:center; width:48%;">
            <div style="color:#d50000; font-weight:bold; font-size:18px;">₹ {total_give:,.0f}</div>
            <div style="color:#757575; font-size:12px;">You'll Give</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("➕\nAdd"): st.session_state.page = 'manual'; st.rerun()
    if c2.button("📅\nDay"): st.session_state.page = 'day_book'; st.rerun()
    if c3.button("📄\nRpt"): st.session_state.page = 'ledger'; st.rerun()
    if c4.button("🎙️\nMic"): st.session_state.page = 'voice'; st.rerun()
    
    c5, c6, c7, c8 = st.columns(4)
    if c5.button("📸\nScan"): st.session_state.page = 'scan_hub'; st.rerun()
    if c6.button("🔔\nRem"): st.session_state.page = 'reminders'; st.rerun()
    if c7.button("⚙️\nTool"): st.session_state.page = 'tools'; st.rerun()
    if c8.button("🔄\nSync"): st.cache_data.clear(); st.rerun()

    st.markdown("---")
    st.markdown("#### Parties")
    search_q = st.text_input("Search Party", placeholder="Search...", label_visibility="collapsed")
    
    sorted_parties = sorted(bals.items(), key=lambda x: x[1], reverse=True)
    
    for party, bal in sorted_parties:
        if abs(bal) < 1: continue 
        if search_q and search_q.lower() not in party.lower(): continue
        
        is_pos = bal > 0
        color_class = "bal-green" if is_pos else "bal-red"
        status_text = "You'll Get" if is_pos else "You'll Give"
        last_dt = dates.get(party, "")
        
        st.markdown(f"""
        <div class="party-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div><div class="party-name">{party}</div><div class="date-text">{last_dt}</div></div>
                <div><div class="{color_class}">₹ {abs(bal):,.0f}</div><div class="sub-text">{status_text}</div></div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        if st.button(f"View {party}", key=f"btn_{party}"):
            st.session_state.selected_party = party
            st.session_state.page = 'ledger'
            st.rerun()

def screen_manual():
    st.markdown("### ➕ Add Transaction")
    if st.button("⬅ Back"): st.session_state.page = 'home'; st.rerun()
    
    with st.container(border=True):
        t_type = st.selectbox("Type", ["Sale (Bill)", "Payment In (Received)", "Purchase (In)", "Payment Out (Paid)"])
        d = st.date_input("Date", date.today())
        p = st.selectbox("Party", get_all_party_names_display())
        a = st.number_input("Amount (₹)", min_value=0.0)
        m = st.text_input("Remarks")
        
        if st.button("Save Transaction", type="primary", use_container_width=True):
            sh = get_sheet_object()
            if "Sale" in t_type: sh.worksheet("CustomerDues").append_row([str(d), p, a])
            elif "Received" in t_type: sh.worksheet("PaymentsReceived").append_row([str(d), p, a, m])
            elif "Paid" in t_type: sh.worksheet("PaymentsToSuppliers").append_row([str(d), p, a, m])
            elif "Purchase" in t_type: sh.worksheet("GoodsReceived").append_row([str(d), p, m, a])
            st.success("Saved!"); time.sleep(1); st.session_state.page = 'home'; st.rerun()

def screen_ledger():
    st.markdown("### 📄 Party Statement")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    idx = 0
    all_p = get_all_party_names_display()
    if 'selected_party' in st.session_state and st.session_state.selected_party in all_p:
        idx = all_p.index(st.session_state.selected_party)
            
    sel = st.selectbox("Select Party", all_p, index=idx)
    
    # RESTORED: Date Controls
    c1, c2, c3 = st.columns(3)
    if c1.button("This Month"): st.session_state['l_s'] = date.today().replace(day=1); st.rerun()
    if c2.button("Last Month"): st.session_state['l_s'] = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1); st.rerun()
    if c3.button("All Time"): st.session_state['l_s'] = date(2023,1,1); st.rerun()

    if 'l_s' not in st.session_state: st.session_state['l_s'] = date(2025,1,1)
    
    d1, d2 = st.columns(2)
    s = d1.date_input("From", st.session_state['l_s'])
    e = d2.date_input("To", date.today())
    
    if sel:
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        supp = fetch_sheet_data("PaymentsToSuppliers")
        goods = fetch_sheet_data("GoodsReceived")
        
        ledger = []
        # Sales
        sub_s = d_df[d_df["Party"] == sel] if not d_df.empty else pd.DataFrame()
        for _, r in sub_s.iterrows():
            dt = parse_date(str(r.get("Date")))
            if dt and s <= dt <= e: ledger.append({"Date": dt, "Type": "SALE", "Desc": "Bill", "Amount": clean_amount(r.get("Amount")), "DrCr": "Dr"})
        
        # Rx
        sub_p = p_df[p_df["Party"] == sel] if not p_df.empty else pd.DataFrame()
        for _, r in sub_p.iterrows():
            dt = parse_date(str(r.get("Date")))
            if dt and s <= dt <= e: ledger.append({"Date": dt, "Type": "RECEIVED", "Desc": r.get("Mode",""), "Amount": clean_amount(r.get("Amount")), "DrCr": "Cr"})

        # Supplier Logic
        sub_sup = supp[supp["Supplier"] == sel] if not supp.empty else pd.DataFrame()
        for _, r in sub_sup.iterrows():
            dt = parse_date(str(r.get("Date")))
            if dt and s <= dt <= e: ledger.append({"Date": dt, "Type": "PAID", "Desc": "Out", "Amount": clean_amount(r.get("Amount")), "DrCr": "Dr"})
            
        if ledger:
            df = pd.DataFrame(ledger).sort_values("Date")
            running_bal = 0
            df["Balance"] = 0.0
            for i, row in df.iterrows():
                if row["DrCr"] == "Dr": running_bal += row["Amount"]
                else: running_bal -= row["Amount"]
                df.at[i, "Balance"] = running_bal
            
            st.write("---")
            for _, r in df.iterrows():
                color = "red" if r["DrCr"] == "Dr" else "green"
                icon = "🔴" if r["DrCr"] == "Dr" else "🟢"
                st.markdown(f"""
                <div style="background:white; padding:10px; border-radius:8px; margin-bottom:8px; border-left: 5px solid {color}; box-shadow: 0 1px 2px #eee;">
                    <div style="display:flex; justify-content:space-between;">
                        <div style="font-weight:bold; color:#333;">{icon} {r['Type']}</div>
                        <div style="font-weight:bold; color:#333;">₹ {r['Amount']:,.0f}</div>
                    </div>
                    <div style="display:flex; justify-content:space-between; font-size:12px; color:#666;">
                        <div>{r['Date']} | {r['Desc']}</div>
                        <div>Bal: ₹ {r['Balance']:,.0f}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            if st.button("Download PDF"):
                pdf = FPDF()
                pdf.add_page(); pdf.set_font("Arial", size=12)
                pdf.cell(200, 10, txt=f"Statement: {sel}", ln=True, align='C')
                pdf.set_fill_color(240,240,240)
                pdf.cell(30,10,"Date",1); pdf.cell(80,10,"Desc",1); pdf.cell(40,10,"Amount",1); pdf.cell(40,10,"Balance",1,1)
                for _, r in df.iterrows():
                    pdf.cell(30,10,str(r['Date']),1)
                    pdf.cell(80,10,str(r['Desc']),1)
                    pdf.cell(40,10,str(r['Amount']),1)
                    pdf.cell(40,10,str(r['Balance']),1,1)
                st.download_button("Download PDF", pdf.output(dest='S').encode('latin-1'), "stmt.pdf")
        else: st.info("No transactions found.")

def screen_day_book():
    st.markdown("### 📅 Day Book")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    dt = st.date_input("Date", date.today())
    
    d_df = fetch_sheet_data("CustomerDues")
    p_df = fetch_sheet_data("PaymentsReceived")
    s_df = fetch_sheet_data("PaymentsToSuppliers")
    
    day_s = [r for _, r in d_df.iterrows() if parse_date(str(r.get("Date"))) == dt] if not d_df.empty else []
    day_p = [r for _, r in p_df.iterrows() if parse_date(str(r.get("Date"))) == dt] if not p_df.empty else []
    day_sup = [r for _, r in s_df.iterrows() if parse_date(str(r.get("Date"))) == dt] if not s_df.empty else []
                
    st.metric("Total Sales", f"₹ {sum(clean_amount(x['Amount']) for x in day_s):,.0f}")
    st.metric("Total Received", f"₹ {sum(clean_amount(x['Amount']) for x in day_p):,.0f}")
    
    st.write("---")
    for r in day_s: st.markdown(f"🔴 **Sale**: {r['Party']} - ₹{r['Amount']}")
    for r in day_p: st.markdown(f"🟢 **Received**: {r['Party']} - ₹{r['Amount']}")
    for r in day_sup: st.markdown(f"🟠 **Paid Supplier**: {r['Supplier']} - ₹{r['Amount']}")

# --- RESTORED: FULL SCANNER HUB (All 4 Tabs) ---
def screen_scan_hub():
    st.markdown("### 📸 Scanner Hub")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    # 4 Tabs Restored
    t1, t2, t3, t4 = st.tabs(["Journal", "Ledger", "Bank", "Bill"])
    
    with t1: # Journal
        img = st.file_uploader("Upload Journal", type=['jpg','png'], key="j_upl")
        if img and st.button("Process Journal"):
            with st.spinner("Processing..."):
                link = upload_to_drive(compress_image(img), f"Journal_{date.today()}.jpg")
                img.seek(0)
                p = """Analyze image. Extract Date. Identify Sales and Payments. Return JSON: {"Date": "YYYY-MM-DD", "Sales": [{"Party": "Name", "Amount": 0}], "Payments": [{"Party": "Name", "Amount": 0}]}"""
                data = analyze_image_generic(p, img.read())
                if data: st.session_state.scan_res = data; st.session_state.scan_link = link; st.rerun()

    with t2: # Ledger
        img = st.file_uploader("Upload Old Ledger", type=['jpg','png'], key="l_upl")
        if img and st.button("Digitize Ledger"):
            with st.spinner("Digitizing..."):
                img.seek(0)
                p = """Analyze Ledger Page. Return JSON: {"Date": "YYYY-MM-DD", "Sales": [{"Party": "Name", "Amount": 0}], "Payments": []}"""
                data = analyze_image_generic(p, img.read())
                if data: st.session_state.scan_res = data; st.session_state.scan_link = "Ledger Scan"; st.rerun()

    with t3: # Bank
        img = st.file_uploader("Bank Receipt", type=['jpg','png'], key="b_upl")
        if img and st.button("Check Receipt"):
            with st.spinner("Checking..."):
                img.seek(0)
                p = """Analyze Receipt. Return JSON: {"Date": "YYYY-MM-DD", "Sales": [], "Payments": [{"Party": "Sender Name", "Amount": 0}]}"""
                data = analyze_image_generic(p, img.read())
                if data: st.session_state.scan_res = data; st.session_state.scan_link = "Bank Scan"; st.rerun()

    with t4: # Bill
        img = st.file_uploader("Upload Bill", type=['jpg','png'], key="bi_upl")
        if img and st.button("Read Bill"):
            with st.spinner("Reading..."):
                img.seek(0)
                p = """Analyze Purchase Bill. Return JSON: {"Date": "YYYY-MM-DD", "Sales": [], "Payments": [{"Party": "Vendor Name", "Amount": 0}]}""" # Treating purchase as payment out for simplicity in this schema
                data = analyze_image_generic(p, img.read())
                if data: st.session_state.scan_res = data; st.session_state.scan_link = "Bill Scan"; st.rerun()

    # --- RESULT REVIEW & SAVE ---
    if 'scan_res' in st.session_state:
        d = st.session_state.scan_res
        st.write("### Review")
        dt = st.date_input("Date", parse_date(d.get("Date")) or date.today())
        
        st.write("Sales Detected:")
        df_s = pd.DataFrame(d.get("Sales", []))
        ed_s = st.data_editor(df_s, num_rows="dynamic")
        
        if st.button("💾 Save Scanned Data"):
            sh = get_sheet_object()
            rows_s = [[str(dt), r['Party'], r['Amount']] for _, r in ed_s.iterrows()]
            if rows_s: sh.worksheet("CustomerDues").append_rows(rows_s)
            
            rows_p = [[str(dt), r['Party'], r['Amount']] for _, r in ed_p.iterrows()]
            if rows_p: sh.worksheet("PaymentsReceived").append_rows(rows_p)
            
            st.success("Saved!"); del st.session_state.scan_res; st.rerun()

def screen_voice_assistant():
    st.markdown("### 🎙️ AI Voice")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    if not VOICE_AVAILABLE: st.error("Voice not supported."); return
    audio = mic_recorder(start_prompt="🎤 Tap to Speak", stop_prompt="⏹️ Stop", key='mic')
    
    if audio:
        with st.spinner("Listening..."):
            try:
                api_key = st.secrets["OPENAI_API_KEY"]
                client = OpenAI(api_key=api_key)
                ab = io.BytesIO(audio['bytes']); ab.name = "audio.wav"
                txt = client.audio.transcriptions.create(model="whisper-1", file=ab).text
                st.info(f"You said: {txt}")
                
                p = f"Command: {txt}. Return JSON: {{'intent': 'view_ledger' or 'add_txn', 'party': 'Name', 'amount': 0}}"
                resp = client.chat.completions.create(model="gpt-4o", messages=[{"role":"user", "content":p}])
                res = json.loads(resp.choices[0].message.content)
                
                if res.get('intent') == 'view_ledger':
                    st.session_state.selected_party = res['party']
                    st.session_state.page = 'ledger'
                    st.rerun()
                else:
                    st.success(f"Detected: {res}")
            except Exception as e: st.error(str(e))

# --- RESTORED: FULL REMINDERS (With Sort) ---
def screen_reminders():
    st.markdown("### 🔔 WhatsApp Reminders")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    bals, _ = get_party_balances()
    due_list = [{"Party": p, "Due": v} for p, v in bals.items() if v > 1]
    
    # Sort Buttons
    c1, c2 = st.columns(2)
    if c1.button("Sort: High Amount"): due_list.sort(key=lambda x: x['Due'], reverse=True)
    if c2.button("Sort: A-Z"): due_list.sort(key=lambda x: x['Party'])
    
    df = pd.DataFrame(due_list)
    st.dataframe(df, use_container_width=True)
    
    st.write("---")
    for _, r in df.iterrows():
        msg = f"Hello {r['Party']}, Your pending balance is ₹ {r['Due']:,.0f}. Please pay soon."
        link = f"https://wa.me/?text={urllib.parse.quote(msg)}"
        st.link_button(f"Send to {r['Party']}", link)

# --- RESTORED: FULL TOOLS (4 Tabs) ---
def screen_tools():
    st.markdown("### ⚙️ Tools")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    t1, t2, t3, t4 = st.tabs(["Merge Party", "Edit Data", "Master List", "Reset"])
    
    with t1: # Merge
        parties = get_all_party_names_display()
        old = st.selectbox("Old Name", parties, index=None)
        new = st.selectbox("New Name", parties, index=None)
        if st.button("Merge Now") and old and new:
            sh = get_sheet_object()
            for s in ["CustomerDues", "PaymentsReceived"]:
                ws = sh.worksheet(s); vals = ws.get_all_values()
                ups = [{"range": f"B{i+1}", "values": [[new]]} for i, r in enumerate(vals) if len(r)>1 and r[1]==old]
                if ups: ws.batch_update(ups)
            st.success("Merged!")

    with t2: # Edit
        sheet = st.selectbox("Select Sheet", ["CustomerDues", "PaymentsReceived"])
        if st.button("Load Data"):
            st.session_state.tool_df = fetch_sheet_data(sheet)
        
        if 'tool_df' in st.session_state:
            ed = st.data_editor(st.session_state.tool_df, num_rows="dynamic")
            if st.button("Save Changes"):
                sh = get_sheet_object(); ws = sh.worksheet(sheet); ws.clear()
                ws.update([ed.columns.tolist()] + ed.astype(str).values.tolist())
                st.success("Updated!")

    with t3: # Master
        df_m = fetch_sheet_data("Party_Master")
        ed_m = st.data_editor(df_m, num_rows="dynamic")
        if st.button("Save Master List"):
            sh = get_sheet_object(); ws = sh.worksheet("Party_Master"); ws.clear()
            ws.update([ed_m.columns.tolist()] + ed_m.astype(str).values.tolist())
            st.success("Saved!")

    with t4: # Reset
        if st.button("🧨 Factory Reset", disabled=(st.text_input("Type WIPE")!="WIPE")):
            sh = get_sheet_object()
            for s in ["CustomerDues", "PaymentsReceived"]: sh.worksheet(s).clear()
            st.success("Reset Complete!")

# --- MAIN APP LOGIC ---
try:
    if 'page' not in st.session_state: st.session_state.page = 'home'
    
    if st.session_state.page == 'home': screen_home()
    elif st.session_state.page == 'manual': screen_manual()
    elif st.session_state.page == 'day_book': screen_day_book()
    elif st.session_state.page == 'ledger': screen_ledger()
    elif st.session_state.page == 'scan_hub': screen_scan_hub()
    elif st.session_state.page == 'voice': screen_voice_assistant()
    elif st.session_state.page == 'reminders': screen_reminders()
    elif st.session_state.page == 'tools': screen_tools()

except Exception as e:
    st.error("Something went wrong")
    st.code(traceback.format_exc())
