import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI
from datetime import date
import json
from fpdf import FPDF
import base64
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Gautam Pharma AI", layout="wide", page_icon="💊")

# --- 1. ROBUST GOOGLE SHEETS CONNECTION ---
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

def get_sheet_object():
    """Returns the spreadsheet object once to avoid repeated API calls."""
    client = get_gsheet_client()
    if client:
        return client.open("Gautam_Pharma_Ledger")
    return None

def get_all_party_names():
    """Fetches unique party names from all sheets for Autocomplete."""
    names = set()
    try:
        sh = get_sheet_object()
        # check customer dues
        try: names.update(sh.worksheet("CustomerDues").col_values(2)[1:]) 
        except: pass
        # check payments
        try: names.update(sh.worksheet("PaymentsReceived").col_values(2)[1:]) 
        except: pass
        # check suppliers
        try: names.update(sh.worksheet("GoodsReceived").col_values(2)[1:]) 
        except: pass
    except: pass
    return sorted(list(names))

# --- 2. AI EXTRACTION ---
def run_ai_extraction(image_bytes):
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
        client = OpenAI(api_key=api_key)
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        
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
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}],
            max_tokens=1000
        )
        content = response.choices[0].message.content.replace("```json", "").replace("```", "")
        return json.loads(content)
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

# --- 3. PDF GENERATOR ---
def generate_ledger_pdf(party_name, dataframe, total_due):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(190, 10, "Gautam Pharma - Ledger Statement", ln=True, align='C')
    
    pdf.set_font("Arial", '', 12)
    pdf.cell(190, 10, f"Party Name: {party_name}", ln=True, align='L')
    pdf.cell(190, 10, f"Date: {date.today()}", ln=True, align='L')
    pdf.ln(10)
    
    # Table Header
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(30, 10, "Date", 1)
    pdf.cell(80, 10, "Description", 1)
    pdf.cell(30, 10, "Debit", 1)
    pdf.cell(30, 10, "Credit", 1)
    pdf.ln()
    
    # Table Rows
    pdf.set_font("Arial", '', 10)
    for _, row in dataframe.iterrows():
        pdf.cell(30, 10, str(row['Date']), 1)
        pdf.cell(80, 10, str(row['Description'])[:35], 1)
        pdf.cell(30, 10, str(row['Debit']), 1)
        pdf.cell(30, 10, str(row['Credit']), 1)
        pdf.ln()
        
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    status = "RECEIVABLE (They Owe You)" if total_due > 0 else "PAYABLE (You Owe Them)"
    pdf.cell(190, 10, f"Net Balance: Rs. {total_due}  [{status}]", ln=True)
    
    return pdf.output(dest='S').encode('latin-1')

# --- 4. TABS & UI ---

def tab_scan_ai():
    st.header("📸 AI Journal Scanner")
    img_file = st.file_uploader("Upload Ledger Photo", type=["jpg", "png", "jpeg"])
    
    if img_file and st.button("🚀 Extract Data"):
        with st.spinner("AI is reading all sections..."):
            data = run_ai_extraction(img_file.read())
            if data:
                st.session_state['extracted_data'] = data
                st.success("Extraction Complete! Review below.")

    if 'extracted_data' in st.session_state:
        data = st.session_state['extracted_data']
        st.divider()
        st.subheader("📝 Review & Save")
        
        with st.form("review_form"):
            # 1. Dues
            st.markdown("**1. Retailers Dues**")
            dues = data.get("CustomerDues", [])
            final_dues = []
            for i, d in enumerate(dues):
                c1, c2 = st.columns([3, 1])
                final_dues.append({"Party": c1.text_input("Party", d.get("Party"), key=f"d_p_{i}"), "Amount": c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"d_a_{i}")})
            
            # 2. Payments
            st.markdown("**2. Payments Received**")
            rx = data.get("PaymentsReceived", [])
            final_rx = []
            for i, d in enumerate(rx):
                c1, c2, c3 = st.columns([2, 1, 1])
                final_rx.append({"Party": c1.text_input("Party", d.get("Party"), key=f"r_p_{i}"), "Amount": c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"r_a_{i}"), "Mode": c3.selectbox("Mode", ["Cash", "UPI"], key=f"r_m_{i}")})

            # 3. Supplier Payments
            st.markdown("**3. Payments To Suppliers**")
            tx = data.get("PaymentsToSuppliers", [])
            final_tx = []
            for i, d in enumerate(tx):
                c1, c2, c3 = st.columns([2, 1, 1])
                final_tx.append({"Supplier": c1.text_input("Supplier", d.get("Supplier"), key=f"t_s_{i}"), "Amount": c2.number_input("Amount", value=float(d.get("Amount", 0)), key=f"t_a_{i}"), "Mode": c3.selectbox("Mode", ["Cash", "UPI"], key=f"t_m_{i}")})
            
            # 4. Purchases
            st.markdown("**4. Purchases (Goods Rx)**")
            gx = data.get("GoodsReceived", [])
            final_gx = []
            for i, d in enumerate(gx):
                c1, c2, c3 = st.columns([2, 2, 1])
                final_gx.append({"Supplier": c1.text_input("Supplier", d.get("Supplier"), key=f"g_s_{i}"), "Items": c2.text_input("Items", d.get("Items", "Goods"), key=f"g_i_{i}"), "Amount": c3.number_input("Amount", value=float(d.get("Amount", 0)), key=f"g_a_{i}")})

            if st.form_submit_button("💾 Save All Data"):
                sh = get_sheet_object()
                txn_date = data.get("Date", str(date.today()))
                
                # Batch save to avoid API limits
                try:
                    if final_dues: 
                        sh.worksheet("CustomerDues").append_rows([[txn_date, r["Party"], r["Amount"]] for r in final_dues if r["Party"]])
                    if final_rx: 
                        sh.worksheet("PaymentsReceived").append_rows([[txn_date, r["Party"], r["Amount"], r["Mode"]] for r in final_rx if r["Party"]])
                    if final_tx: 
                        sh.worksheet("PaymentsToSuppliers").append_rows([[txn_date, r["Supplier"], r["Amount"], r["Mode"]] for r in final_tx if r["Supplier"]])
                    if final_gx: 
                        sh.worksheet("GoodsReceived").append_rows([[txn_date, r["Supplier"], r["Items"], r["Amount"]] for r in final_gx if r["Supplier"]])
                    
                    st.success("✅ All data saved successfully!")
                    del st.session_state['extracted_data']
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")

def tab_ledger_view():
    st.header("📒 Party Ledger & Export")
    
    # Party Selection with Autocomplete
    all_parties = get_all_party_names()
    sel_party = st.selectbox("Select Party to View", ["Select..."] + all_parties)
    
    if sel_party != "Select...":
        sh = get_sheet_object()
        ledger_data = []
        
        # 1. Get Dues (Debit)
        try:
            d_df = pd.DataFrame(sh.worksheet("CustomerDues").get_all_records())
            p_dues = d_df[d_df['Party'] == sel_party]
            for _, r in p_dues.iterrows(): ledger_data.append({"Date": r['Date'], "Description": "Goods Sold / Due", "Debit": r['Amount'], "Credit": 0})
        except: pass
        
        # 2. Get Payments (Credit)
        try:
            p_df = pd.DataFrame(sh.worksheet("PaymentsReceived").get_all_records())
            p_rx = p_df[p_df['Party'] == sel_party]
            for _, r in p_rx.iterrows(): ledger_data.append({"Date": r['Date'], "Description": f"Payment Rx ({r['Mode']})", "Debit": 0, "Credit": r['Amount']})
        except: pass

        # 3. Create DataFrame
        if ledger_data:
            l_df = pd.DataFrame(ledger_data).sort_values(by="Date")
            
            # Calculate Balance
            total_debit = l_df["Debit"].sum()
            total_credit = l_df["Credit"].sum()
            net_bal = total_debit - total_credit
            
            # Display Stats
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Debit (Sold)", f"₹{total_debit}")
            col2.metric("Total Credit (Recvd)", f"₹{total_credit}")
            col3.metric("Net Balance", f"₹{net_bal}", delta_color="inverse")
            
            # Show Table
            st.dataframe(l_df, use_container_width=True)
            
            # PDF Button
            pdf_bytes = generate_ledger_pdf(sel_party, l_df, net_bal)
            st.download_button("⬇️ Download PDF Statement", data=pdf_bytes, file_name=f"{sel_party}_ledger.pdf", mime="application/pdf")
        else:
            st.info("No transactions found for this party.")

def tab_manage_data():
    st.header("✏️ View & Edit Saved Data")
    st.info("You can edit values directly here. Click 'Update Google Sheet' to save changes.")
    
    sheet_choice = st.selectbox("Select Sheet to Edit", ["CustomerDues", "PaymentsReceived", "PaymentsToSuppliers", "GoodsReceived"])
    
    if st.button("Load Data"):
        sh = get_sheet_object()
        try:
            ws = sh.worksheet(sheet_choice)
            df = pd.DataFrame(ws.get_all_records())
            st.session_state['edit_df'] = df
            st.session_state['edit_sheet_name'] = sheet_choice
        except:
            st.error("Sheet not found or empty.")

    if 'edit_df' in st.session_state and st.session_state['edit_sheet_name'] == sheet_choice:
        # The Magic Edit Table
        edited_df = st.data_editor(st.session_state['edit_df'], num_rows="dynamic")
        
        if st.button("💾 Update Google Sheet Now"):
            try:
                sh = get_sheet_object()
                ws = sh.worksheet(sheet_choice)
                # Clear and rewrite
                ws.clear()
                ws.update([edited_df.columns.values.tolist()] + edited_df.values.tolist())
                st.success("✅ Google Sheet Updated!")
            except Exception as e:
                st.error(f"Update failed: {e}")

def tab_manual_entry():
    st.header("⌨️ Manual Entry")
    
    all_parties = get_all_party_names()
    
    with st.form("manual_add"):
        c1, c2 = st.columns(2)
        
        # Autocomplete Dropdown
        party_input = c1.selectbox("Existing Party", ["Select...", "➕ Add New"] + all_parties)
        
        if party_input == "➕ Add New":
            final_name = c1.text_input("Enter New Party Name")
        elif party_input == "Select...":
            final_name = ""
        else:
            final_name = party_input
            
        entry_type = c2.selectbox("Entry Type", ["Customer Due (Udhaari)", "Payment Received (Jama)", "Supplier Payment", "Purchase (Goods Rx)"])
        amount = c1.number_input("Amount", min_value=0.0)
        mode = c2.selectbox("Mode (if payment)", ["Cash", "UPI", "Cheque"])
        desc = c1.text_input("Description / Items", "Goods")
        date_val = c2.date_input("Date", date.today())
        
        if st.form_submit_button("Save Entry"):
            if not final_name or amount == 0:
                st.warning("Please enter Name and Amount")
            else:
                sh = get_sheet_object()
                try:
                    if entry_type == "Customer Due (Udhaari)":
                        sh.worksheet("CustomerDues").append_row([str(date_val), final_name, amount])
                    elif entry_type == "Payment Received (Jama)":
                        sh.worksheet("PaymentsReceived").append_row([str(date_val), final_name, amount, mode])
                    elif entry_type == "Supplier Payment":
                        sh.worksheet("PaymentsToSuppliers").append_row([str(date_val), final_name, amount, mode])
                    elif entry_type == "Purchase (Goods Rx)":
                        sh.worksheet("GoodsReceived").append_row([str(date_val), final_name, desc, amount])
                    st.success("Saved!")
                except Exception as e:
                    st.error(f"Error: {e}")

# --- MAIN ---
def main():
    st.sidebar.title("💊 Gautam Pharma")
    menu = st.sidebar.radio("Navigate", ["Scan (AI)", "Manual Entry", "📒 Ledger & PDF", "✏️ Edit Saved Data"])
    
    if menu == "Scan (AI)": tab_scan_ai()
    elif menu == "Manual Entry": tab_manual_entry()
    elif menu == "📒 Ledger & PDF": tab_ledger_view()
    elif menu == "✏️ Edit Saved Data": tab_manage_data()

if __name__ == "__main__":
    main()
