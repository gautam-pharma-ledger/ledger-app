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
        transition: transform 0.1s;
    }
    .party-card:active { transform: scale(0.98); background-color: #f5f5f5; }
    
    /* 4. Balance Colors */
    .bal-green { color: #00c853; font-weight: 700; font-size: 16px; text-align: right; }
    .bal-red { color: #d50000; font-weight: 700; font-size: 16px; text-align: right; }
    .sub-text { font-size: 12px; color: #757575; text-align: right; }
    .party-name { font-size: 16px; font-weight: 600; color: #333; }
    .date-text { font-size: 12px; color: #9e9e9e; }

    /* 5. Buttons (Rounded & Colorful) */
    .stButton>button {
        border-radius: 25px;
        font-weight: 600;
        border: none;
        box-shadow: 0 2px 5px rgba(0,0,0,0.1);
    }
    /* Primary Action Button (Red - Add New) */
    .add-btn > button {
        background-color: #d50000; color: white; height: 3em;
    }
    .add-btn > button:hover { background-color: #b71c1c; color: white; }

    /* 6. Quick Links (Circle Icons) */
    .quick-link {
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        background: white; border-radius: 15px; padding: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05); cursor: pointer;
    }
    
    /* 7. Bottom Navigation (Simulated) */
    .bottom-nav {
        position: fixed; bottom: 0; left: 0; width: 100%;
        background: white; border-top: 1px solid #eee;
        display: flex; justify-content: space-around; padding: 10px 0;
        z-index: 999;
    }
    .nav-item { text-align: center; font-size: 12px; color: #757575; cursor: pointer; }
    
    /* Hide Streamlit Elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# --- 1. CONNECTORS ---
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
        df.replace("", pd.NA, inplace=True)
        df.dropna(how='all', inplace=True)
        df.fillna("", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        if sheet_name in ["PaymentsToSuppliers", "GoodsReceived"]:
            if "Party" in df.columns: df.rename(columns={"Party": "Supplier"}, inplace=True)
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

# --- 2. LOGIC: CALCULATE BALANCES ---
def get_party_balances():
    dues = fetch_sheet_data("CustomerDues")
    pymt = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    supp = fetch_sheet_data("PaymentsToSuppliers")
    
    balances = {}
    last_dates = {}

    # Sales & Rx
    if not dues.empty:
        for _, r in dues.iterrows():
            p = r.get("Party"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) + amt
            last_dates[p] = r.get("Date")
    if not pymt.empty:
        for _, r in pymt.iterrows():
            p = r.get("Party"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) - amt
            last_dates[p] = r.get("Date") # Update last interaction

    # Suppliers
    if not goods.empty:
        for _, r in goods.iterrows():
            p = r.get("Supplier"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) - amt # Payable is negative in this logic
    if not supp.empty:
        for _, r in supp.iterrows():
            p = r.get("Supplier"); amt = clean_amount(r.get("Amount"))
            balances[p] = balances.get(p, 0) + amt

    return balances, last_dates

# --- 3. SCREENS ---

def screen_home():
    # 1. Header & Summary
    st.markdown("### 💊 Gautam Pharma")
    
    bals, dates = get_party_balances()
    total_get = sum([v for v in bals.values() if v > 0])
    total_give = sum([abs(v) for v in bals.values() if v < 0])
    
    # Summary Box
    with st.container():
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

    # 2. Quick Links (Buttons)
    c1, c2, c3, c4 = st.columns(4)
    if c1.button("➕\nAdd"): st.session_state.page = 'manual'; st.rerun()
    if c2.button("📅\nDayBook"): st.session_state.page = 'day_book'; st.rerun()
    if c3.button("📄\nReport"): st.session_state.page = 'ledger'; st.rerun()
    if c4.button("🎙️\nVoice"): st.session_state.page = 'voice'; st.rerun()

    # 3. Search & Add Button
    st.markdown("---")
    col_search, col_add = st.columns([3, 1])
    search_q = col_search.text_input("Search Party", placeholder="Search...", label_visibility="collapsed")
    
    # "Add New Party" simulated button (goes to entry)
    if col_add.button("New +", type="primary", use_container_width=True):
        st.session_state.page = 'manual'; st.rerun()

    # 4. Party List (The Khatabook Look)
    st.markdown("#### Parties")
    
    # Sort: Positive balances first
    sorted_parties = sorted(bals.items(), key=lambda x: x[1], reverse=True)
    
    for party, bal in sorted_parties:
        if abs(bal) < 1: continue # Skip zero balance
        
        # Filter
        if search_q and search_q.lower() not in party.lower(): continue
        
        # Determine Color & Text
        is_pos = bal > 0
        color_class = "bal-green" if is_pos else "bal-red"
        status_text = "You'll Get" if is_pos else "You'll Give"
        last_dt = dates.get(party, "")
        
        # HTML Card
        card_html = f"""
        <div class="party-card">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div class="party-name">{party}</div>
                    <div class="date-text">{last_dt}</div>
                </div>
                <div>
                    <div class="{color_class}">₹ {abs(bal):,.0f}</div>
                    <div class="sub-text">{status_text}</div>
                </div>
            </div>
        </div>
        """
        # Make the card clickable via a hidden button trick or just a button below it
        # Streamlit doesn't support clickable HTML divs easily. 
        # Workaround: Render HTML, then a small "View" button right below or inside a container.
        
        with st.container():
            st.markdown(card_html, unsafe_allow_html=True)
            if st.button(f"View {party}", key=f"btn_{party}"):
                st.session_state.selected_party = party
                st.session_state.page = 'ledger'
                st.rerun()

def screen_manual():
    st.markdown("### ➕ Add Transaction")
    if st.button("⬅ Back"): st.session_state.page = 'home'; st.rerun()
    
    with st.container(border=True):
        t_type = st.selectbox("Transaction Type", ["Sale (Bill)", "Payment In (Received)", "Purchase (In)", "Payment Out (Paid)"])
        
        # Color coding the form
        if "Sale" in t_type: st.info("Customer will owe you money (Green)")
        elif "Received" in t_type: st.success("Customer is paying you")
        elif "Paid" in t_type: st.warning("You are paying a supplier")
        
        d = st.date_input("Date", date.today())
        p = st.selectbox("Select Party", get_all_party_names_display())
        a = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
        m = st.text_input("Remarks / Item Details")
        
        if st.button("Save Transaction", type="primary", use_container_width=True):
            sh = get_sheet_object()
            if "Sale" in t_type: 
                sh.worksheet("CustomerDues").append_row([str(d), p, a])
            elif "Received" in t_type: 
                sh.worksheet("PaymentsReceived").append_row([str(d), p, a, m])
            elif "Paid" in t_type:
                sh.worksheet("PaymentsToSuppliers").append_row([str(d), p, a, m])
            elif "Purchase" in t_type:
                sh.worksheet("GoodsReceived").append_row([str(d), p, m, a])
            
            st.success("Saved Successfully!")
            time.sleep(1)
            st.session_state.page = 'home'
            st.rerun()

def screen_ledger():
    st.markdown("### 📄 Party Statement")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    # Auto-select if clicked from Home
    idx = 0
    all_p = get_all_party_names_display()
    if 'selected_party' in st.session_state:
        if st.session_state.selected_party in all_p:
            idx = all_p.index(st.session_state.selected_party)
            
    sel = st.selectbox("Select Party", all_p, index=idx)
    
    # Date Filter
    c1, c2 = st.columns(2)
    s = c1.date_input("Start", date(2025,1,1))
    e = c2.date_input("End", date.today())
    
    if sel:
        d_df = fetch_sheet_data("CustomerDues")
        p_df = fetch_sheet_data("PaymentsReceived")
        
        ledger = []
        # Fetch Sales
        sub_s = d_df[d_df["Party"] == sel] if not d_df.empty else pd.DataFrame()
        for _, r in sub_s.iterrows():
            dt = parse_date(str(r.get("Date")))
            if dt and s <= dt <= e:
                ledger.append({"Date": dt, "Type": "SALE", "Desc": "Bill", "Amount": clean_amount(r.get("Amount")), "DrCr": "Dr"})
        
        # Fetch Payments
        sub_p = p_df[p_df["Party"] == sel] if not p_df.empty else pd.DataFrame()
        for _, r in sub_p.iterrows():
            dt = parse_date(str(r.get("Date")))
            if dt and s <= dt <= e:
                ledger.append({"Date": dt, "Type": "PAYMENT", "Desc": r.get("Mode",""), "Amount": clean_amount(r.get("Amount")), "DrCr": "Cr"})
                
        if ledger:
            df = pd.DataFrame(ledger).sort_values("Date")
            
            # Running Balance Calculation
            running_bal = 0
            df["Balance"] = 0.0
            for i, row in df.iterrows():
                if row["DrCr"] == "Dr": running_bal += row["Amount"]
                else: running_bal -= row["Amount"]
                df.at[i, "Balance"] = running_bal
            
            # Display Cards for Transactions
            st.write("---")
            for _, r in df.iterrows():
                color = "red" if r["DrCr"] == "Dr" else "green" # Red for Sale (Due), Green for Pay
                icon = "🔴" if r["DrCr"] == "Dr" else "🟢"
                
                st.markdown(f"""
                <div style="background:white; padding:10px; border-radius:8px; margin-bottom:8px; border-left: 5px solid {color}; box-shadow: 0 1px 2px #eee;">
                    <div style="display:flex; justify-content:space-between;">
                        <div style="font-weight:bold;">{icon} {r['Type']}</div>
                        <div style="font-weight:bold;">₹ {r['Amount']:,.0f}</div>
                    </div>
                    <div style="display:flex; justify-content:space-between; font-size:12px; color:#666;">
                        <div>{r['Date']} | {r['Desc']}</div>
                        <div>Bal: ₹ {r['Balance']:,.0f}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            # PDF Button
            if st.button("Download PDF Statement"):
                pdf = FPDF()
                pdf.add_page(); pdf.set_font("Arial", size=12)
                pdf.cell(200, 10, txt=f"Statement: {sel}", ln=True, align='C')
                for _, r in df.iterrows():
                    pdf.cell(0, 10, f"{r['Date']} | {r['Type']} | {r['Amount']} | Bal: {r['Balance']}", ln=True)
                
                st.download_button("Download PDF", pdf.output(dest='S').encode('latin-1'), "stmt.pdf")

        else:
            st.info("No transactions found.")

def screen_day_book():
    st.markdown("### 📅 Day Book")
    if st.button("🏠 Home"): st.session_state.page = 'home'; st.rerun()
    
    dt = st.date_input("Date", date.today())
    
    # Fetch all
    d_df = fetch_sheet_data("CustomerDues")
    p_df = fetch_sheet_data("PaymentsReceived")
    
    # Filter
    day_s = []
    if not d_df.empty:
        for _, r in d_df.iterrows():
            if parse_date(str(r.get("Date"))) == dt:
                day_s.append(r)
    
    day_p = []
    if not p_df.empty:
        for _, r in p_df.iterrows():
            if parse_date(str(r.get("Date"))) == dt:
                day_p.append(r)
                
    st.metric("Total Sales", f"₹ {sum(clean_amount(x['Amount']) for x in day_s):,.0f}")
    st.metric("Total Received", f"₹ {sum(clean_amount(x['Amount']) for x in day_p):,.0f}")
    
    st.subheader("Transactions")
    if not day_s and not day_p: st.caption("No entries today.")
    
    for r in day_s:
        st.markdown(f"🔴 **Sale**: {r['Party']} - ₹{r['Amount']}")
    for r in day_p:
        st.markdown(f"🟢 **Received**: {r['Party']} - ₹{r['Amount']}")

# --- MAIN ROUTER ---
if 'page' not in st.session_state: st.session_state.page = 'home'

# Sidebar Menu (Hidden mostly, but useful for Tools)
with st.sidebar:
    st.title("Menu")
    if st.button("Home"): st.session_state.page = 'home'; st.rerun()
    if st.button("Scan Hub"): st.session_state.page = 'scan_hub'; st.rerun()
    if st.button("Tools/Reset"): st.session_state.page = 'tools'; st.rerun()

try:
    if st.session_state.page == 'home': screen_home()
    elif st.session_state.page == 'manual': screen_manual()
    elif st.session_state.page == 'ledger': screen_ledger()
    elif st.session_state.page == 'day_book': screen_day_book()
    # (Other pages like scan/tools use basic Streamlit UI for now to save space)
    elif st.session_state.page == 'voice': 
        st.info("Voice Feature"); st.button("Back", on_click=lambda: setattr(st.session_state, 'page', 'home'))
    elif st.session_state.page == 'scan_hub': st.info("Scan Hub (Under Construction for New UI)"); st.button("Back", on_click=lambda: setattr(st.session_state, 'page', 'home'))
    elif st.session_state.page == 'tools': st.info("Tools"); st.button("Back", on_click=lambda: setattr(st.session_state, 'page', 'home'))

except Exception as e:
    st.error("Something went wrong.")
    st.exception(e)
