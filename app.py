import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from datetime import date
import json
from fpdf import FPDF
import base64

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma AI", layout="wide", page_icon="💊")

# --- 1. GOOGLE SHEETS CONNECTION ---
def get_gsheet_client():
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        return gspread.authorize(credentials)
    except Exception as e:
        st.error(f"❌ Connection Error: {e}")
        return None

def get_sheet_data(tab_name):
    """Reads data from a specific Google Sheet tab."""
    try:
        client = get_gsheet_client()
        sh = client.open("Gautam_Pharma_Ledger")
        try:
            ws = sh.worksheet(tab_name)
            data = ws.get_all_records()
            return pd.DataFrame(data)
        except gspread.exceptions.WorksheetNotFound:
            return pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()

def append_to_sheet(tab_name, row_data):
    """Appends a list of values to a Google Sheet tab."""
    try:
        client = get_gsheet_client()
        sh = client.open("Gautam_Pharma_Ledger")
        ws = sh.worksheet(tab_name)
        ws.append_row(row_data)
        return True
    except Exception as e:
        st.error(f"❌ Save Failed to {tab_name}: {e}")
        return False

# --- 2. AI EXTRACTION ENGINE ---
def run_ai_extraction(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
        # We explicitly tell AI how to map your specific handwritten headers
        prompt = """
        Analyze this handwritten journal page. Map the sections as follows:
        - "RETAILERS DUES" -> CustomerDues
        - "PAYMENT RECEIVED" -> PaymentsReceived
        - "PAYMENT TO SUPPLIER" -> PaymentsToSuppliers
        - "PURCHASE DETAILS" -> GoodsReceived

        Return ONLY valid JSON with this structure:
        {
          "Date": "YYYY-MM-DD", 
          "CustomerDues": [{"Party": "Name", "Amount": 0}], 
          "PaymentsReceived": [{"Party": "Name", "Amount": 0, "Mode": "Cash"}], 
          "GoodsReceived": [{"Supplier": "Name", "Items": "Desc", "Amount": 0}], 
          "PaymentsToSuppliers": [{"Supplier": "Name", "Amount": 0, "Mode": "Cash"}] 
        }
        """

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ],
                }
            ],
            max_tokens=1000
        )
        
        content = response.choices[0].message.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
            
        return json.loads(content)

    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

# --- 3. UI MODULES ---

def tab_scan():
    st.header("📸 AI Journal Scanner")
    
    # 1. Upload
    img_file = st.file_uploader("Upload Ledger Photo", type=["jpg", "png", "jpeg"])
    
    if img_file and st.button("🚀 Extract Data"):
        with st.spinner("AI is reading 'Retailers Dues', 'Payments', etc..."):
            data = run_ai_extraction(img_file.read())
            if data:
                st.session_state['extracted_data'] = data
                st.success("Extraction Complete! Scroll down to review.")

    # 2. Review & Edit (FIXED: Now shows ALL sections)
    if 'extracted_data' in st.session_state:
        data = st.session_state['extracted_data']
        st.divider()
        st.subheader("📝 Review & Save")
        
        with st.form("ai_review_form"):
            # A. Customer Dues
            st.markdown("### 1. Retailers Dues (Udhaari)")
            dues = data.get("CustomerDues", [])
            final_dues = []
            if not dues: st.info("No Dues found.")
            for i, d in enumerate(dues):
                c1, c2 = st.columns([3, 1])
                p = c1.text_input(f"Party", d.get("Party"), key=f"d_p_{i}")
                a = c2.number_input(f"Amount", value=float(d.get("Amount", 0)), key=f"d_a_{i}")
                final_dues.append({"Party": p, "Amount": a})
            st.markdown("---")

            # B. Payments Received
            st.markdown("### 2. Payment Received (Jama)")
            rx = data.get("PaymentsReceived", [])
            final_rx = []
            if not rx: st.info("No Payments Received found.")
            for i, d in enumerate(rx):
                c1, c2, c3 = st.columns([2, 1, 1])
                p = c1.text_input(f"Party", d.get("Party"), key=f"r_p_{i}")
                a = c2.number_input(f"Amount", value=float(d.get("Amount", 0)), key=f"r_a_{i}")
                m = c3.selectbox(f"Mode", ["Cash", "UPI", "Cheque"], key=f"r_m_{i}")
                final_rx.append({"Party": p, "Amount": a, "Mode": m})
            st.markdown("---")

            # C. Payments To Supplier
            st.markdown("### 3. Payment To Supplier")
            tx = data.get("PaymentsToSuppliers", [])
            final_tx = []
            if not tx: st.info("No Supplier Payments found.")
            for i, d in enumerate(tx):
                c1, c2, c3 = st.columns([2, 1, 1])
                s = c1.text_input(f"Supplier", d.get("Supplier"), key=f"t_s_{i}")
                a = c2.number_input(f"Amount", value=float(d.get("Amount", 0)), key=f"t_a_{i}")
                m = c3.selectbox(f"Mode", ["Cash", "UPI", "Cheque"], key=f"t_m_{i}")
                final_tx.append({"Supplier": s, "Amount": a, "Mode": m})
            st.markdown("---")

            # D. Purchase Details
            st.markdown("### 4. Purchase Details")
            goods = data.get("GoodsReceived", [])
            final_goods = []
            if not goods: st.info("No Purchases found.")
            for i, d in enumerate(goods):
                c1, c2, c3 = st.columns([2, 2, 1])
                s = c1.text_input(f"Supplier", d.get("Supplier"), key=f"g_s_{i}")
                it = c2.text_input(f"Items", d.get("Items", "Goods"), key=f"g_i_{i}")
                a = c3.number_input(f"Amount", value=float(d.get("Amount", 0)), key=f"g_a_{i}")
                final_goods.append({"Supplier": s, "Items": it, "Amount": a})

            # Save Button
            if st.form_submit_button("💾 Save All Data to Sheets"):
                txn_date = data.get("Date", str(date.today()))
                
                # FIXED: Saving ALL lists now
                for row in final_dues:
                    if row["Party"]: append_to_sheet("CustomerDues", [txn_date, row["Party"], row["Amount"]])
                
                for row in final_rx:
                    if row["Party"]: append_to_sheet("PaymentsReceived", [txn_date, row["Party"], row["Amount"], row["Mode"]])

                for row in final_tx:
                    if row["Supplier"]: append_to_sheet("PaymentsToSuppliers", [txn_date, row["Supplier"], row["Amount"], row["Mode"]])
                
                for row in final_goods:
                    if row["Supplier"]: append_to_sheet("GoodsReceived", [txn_date, row["Supplier"], row["Items"], row["Amount"]])

                st.success("✅ All sections saved successfully!")
                del st.session_state['extracted_data']
                st.rerun()

def tab_view_ledger():
    st.header("📒 View Saved Data")
    st.write("This shows the data currently saved in your Google Sheets.")
    
    tab1, tab2, tab3, tab4 = st.tabs(["Dues Given", "Payments Rx", "Supplier Payments", "Purchases"])
    
    with tab1:
        st.dataframe(get_sheet_data("CustomerDues"), use_container_width=True)
    with tab2:
        st.dataframe(get_sheet_data("PaymentsReceived"), use_container_width=True)
    with tab3:
        st.dataframe(get_sheet_data("PaymentsToSuppliers"), use_container_width=True)
    with tab4:
        st.dataframe(get_sheet_data("GoodsReceived"), use_container_width=True)

def tab_manual():
    st.header("✏️ Manual Entry")
    st.info("Use this tab to add single entries without scanning.")
    # (Simplified manual entry for brevity, Scan is the focus)
    with st.form("quick_add"):
        d_type = st.selectbox("Type", ["Customer Due", "Payment Received"])
        name = st.text_input("Party Name")
        amt = st.number_input("Amount")
        if st.form_submit_button("Save"):
            if d_type == "Customer Due":
                append_to_sheet("CustomerDues", [str(date.today()), name, amt])
            else:
                append_to_sheet("PaymentsReceived", [str(date.today()), name, amt, "Cash"])
            st.success("Saved!")

# --- MAIN APP SHELL ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    
    # ADDED: "View Ledger" to check your data
    menu_options = ["Scan (AI)", "📒 View Ledger", "Manual Entry"]
    choice = st.sidebar.radio("Navigate", menu_options)
    
    if choice == "Scan (AI)":
        tab_scan()
    elif choice == "📒 View Ledger":
        tab_view_ledger()
    elif choice == "Manual Entry":
        tab_manual()

if __name__ == "__main__":
    main()
