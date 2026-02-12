"""
Gautam Pharma Ledger Application
A comprehensive accounting and ledger management system for pharmaceutical businesses.
"""

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
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('gautam_pharma.log')
    ]
)
logger = logging.getLogger(__name__)

# --- SAFETY IMPORTS ---
try:
    import fitz  # PyMuPDF
    PDF_AVAILABLE = True
    logger.info("PyMuPDF (fitz) loaded successfully")
except ImportError:
    PDF_AVAILABLE = False
    logger.warning("PyMuPDF not available - PDF scanning disabled")

try:
    from streamlit_mic_recorder import mic_recorder
    VOICE_AVAILABLE = True
    logger.info("Voice recorder loaded successfully")
except ImportError:
    VOICE_AVAILABLE = False
    logger.warning("Voice recorder not available")

# --- DATA MODELS ---
@dataclass
class Transaction:
    """Data model for transactions"""
    date: date
    party: str
    amount: float
    remarks: str = ""
    transaction_type: str = ""
    
    def validate(self) -> List[str]:
        """Validate transaction data"""
        errors = []
        
        if not self.party or not self.party.strip():
            errors.append("Party name is required")
        
        if self.amount <= 0:
            errors.append("Amount must be greater than zero")
        
        if self.date > datetime.now().date():
            errors.append("Date cannot be in the future")
        
        if self.date < date(2020, 1, 1):
            errors.append("Date seems too old. Please verify.")
        
        return errors

# --- CONFIGURATION ---
st.set_page_config(
    page_title="Gautam Pharma",
    layout="centered",
    page_icon="💊",
    initial_sidebar_state="collapsed"
)

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .stApp { background-color: #f4f7f6 !important; color: #000000 !important; }
    button[data-baseweb="tab"] { background-color: #ffffff !important; color: #000000 !important; border: 1px solid #ddd !important; font-weight: 600 !important; }
    button[data-baseweb="tab"][aria-selected="true"] { background-color: #e3f2fd !important; color: #1565c0 !important; border-color: #1565c0 !important; }
    .stTextInput input, .stDateInput input, .stSelectbox div[data-baseweb="select"] { background-color: #ffffff !important; color: #000000 !important; border: 1px solid #ccc !important; }
    .stButton > button { background-color: #ffffff !important; color: #000000 !important; border: 1px solid #ccc !important; font-weight: bold !important; }
    div[data-testid="stVerticalBlock"] > div > div > div > div > button[kind="primary"] { background-color: #d32f2f !important; color: #ffffff !important; border: none !important; }
    div[data-testid="stFileUploader"] { background-color: #ffffff !important; border: 1px dashed #aaa !important; padding: 10px; border-radius: 8px; }
    div[data-testid="stFileUploader"] span { color: #000 !important; }
    div[data-testid="stFileUploader"] small { color: #333 !important; }
    .party-card { background-color: #ffffff !important; padding: 15px; border-radius: 12px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 10px; border: 1px solid #e0e0e0; }
    .bal-green { color: #2e7d32 !important; font-weight: 700; font-size: 16px; text-align: right; }
    .bal-red { color: #c62828 !important; font-weight: 700; font-size: 16px; text-align: right; }
    div[data-testid="metric-container"] { background-color: white !important; border: 1px solid #eee !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important; }
    div[data-testid="metric-container"] label { color: #555 !important; }
    div[data-testid="metric-container"] div { color: #000 !important; }
    
    /* Splash Screen CSS */
    .splash-container {
        display: flex; justify-content: center; align-items: center;
        height: 70vh; flex-direction: column; animation: fadeOut 2.5s forwards;
    }
    .splash-container img {
        width: 150px; margin-bottom: 20px; border-radius: 20px;
        box-shadow: 0 0 40px rgba(41, 121, 255, 0.25);
    }
    @keyframes fadeOut {
        0% { opacity: 0; transform: scale(0.8); }
        20% { opacity: 1; transform: scale(1); }
        80% { opacity: 1; transform: scale(1); }
        100% { opacity: 0; transform: scale(1.1); }
    }
    </style>
""", unsafe_allow_html=True)

# --- SPLASH SCREEN ---
def show_splash_screen():
    """Display splash screen on first load"""
    if "splash_shown" not in st.session_state:
        splash = st.empty()
        with splash.container():
            logo_url = "https://raw.githubusercontent.com/gautam-pharma-ledger/ledger-app/main/Photoroom-20260102_114853282.png"
            st.markdown(f"""
            <div class="splash-container">
                <img src="{logo_url}">
                <div style="font-size: 26px; color: #2c3e50; font-weight: 700;">Gautam Pharma</div>
            </div>""", unsafe_allow_html=True)
            time.sleep(2.5)
        splash.empty()
        st.session_state["splash_shown"] = True
        logger.info("Splash screen displayed")

# --- GOOGLE SHEETS CONNECTION ---
@st.cache_resource
def get_credentials():
    """Get Google Cloud credentials from Streamlit secrets"""
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        logger.info("Google credentials loaded successfully")
        return creds
    except KeyError as e:
        logger.error(f"Missing credentials in secrets: {e}")
        st.error("❌ Google credentials not configured properly. Check secrets.toml")
        return None
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
        st.error(f"❌ Error loading credentials: {str(e)}")
        return None

@st.cache_resource
def get_gsheet_client():
    """Get authorized gspread client"""
    try:
        creds = get_credentials()
        if not creds:
            return None
        client = gspread.authorize(creds)
        logger.info("Google Sheets client authorized")
        return client
    except Exception as e:
        logger.error(f"Error authorizing gspread client: {e}")
        st.error(f"❌ Cannot connect to Google Sheets: {str(e)}")
        return None

@st.cache_resource
def get_drive_service():
    """Get Google Drive service"""
    try:
        creds = get_credentials()
        if not creds:
            return None
        service = build('drive', 'v3', credentials=creds)
        logger.info("Google Drive service initialized")
        return service
    except Exception as e:
        logger.error(f"Error initializing Drive service: {e}")
        st.error(f"❌ Cannot connect to Google Drive: {str(e)}")
        return None

@st.cache_resource
def get_sheet_object():
    """Get the main spreadsheet object"""
    try:
        client = get_gsheet_client()
        if not client:
            return None
        sheet = client.open("Gautam_Pharma_Ledger")
        logger.info("Spreadsheet 'Gautam_Pharma_Ledger' opened")
        return sheet
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error("Spreadsheet 'Gautam_Pharma_Ledger' not found")
        st.error("❌ Spreadsheet 'Gautam_Pharma_Ledger' not found. Please create it first.")
        return None
    except Exception as e:
        logger.error(f"Error opening spreadsheet: {e}")
        st.error(f"❌ Error opening spreadsheet: {str(e)}")
        return None

# --- DATA FETCHING WITH IMPROVED CACHING ---
@st.cache_data(ttl=30)  # Cache for 30 seconds
def fetch_sheet_data(sheet_name: str) -> pd.DataFrame:
    """
    Fetch data from a specific sheet with comprehensive error handling
    
    Args:
        sheet_name: Name of the worksheet to fetch
        
    Returns:
        DataFrame with the sheet data, or empty DataFrame on error
    """
    try:
        logger.info(f"Fetching data from sheet: {sheet_name}")
        sh = get_sheet_object()
        
        if not sh:
            logger.error("Sheet object not available")
            return pd.DataFrame()
        
        # Check if worksheet exists
        try:
            worksheet = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"Worksheet '{sheet_name}' not found, creating it...")
            # Create worksheet with headers based on sheet type
            headers = get_default_headers(sheet_name)
            worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=len(headers))
            worksheet.append_row(headers)
            logger.info(f"Created new worksheet: {sheet_name}")
            return pd.DataFrame(columns=headers)
        
        # Fetch all data
        data = worksheet.get_all_values()
        
        if not data:
            logger.warning(f"No data in sheet: {sheet_name}")
            return pd.DataFrame()
        
        # First row is headers
        headers = data.pop(0)
        df = pd.DataFrame(data, columns=headers)
        
        # Clean data
        df.replace("", pd.NA, inplace=True)
        df.dropna(how='all', inplace=True)
        df.fillna("", inplace=True)
        df.columns = [str(c).strip() for c in df.columns]
        
        # Normalize column names for consistency
        if sheet_name in ["PaymentsToSuppliers", "GoodsReceived"]:
            if "Party" in df.columns:
                df.rename(columns={"Party": "Supplier"}, inplace=True)
        
        # Clean party/supplier names
        for col in ["Party", "Supplier"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
        
        logger.info(f"Successfully fetched {len(df)} rows from {sheet_name}")
        return df
        
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error for {sheet_name}: {e}")
        st.error(f"❌ API Error: {str(e)}. Please try again later.")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Unexpected error fetching {sheet_name}: {e}")
        logger.error(traceback.format_exc())
        st.error(f"❌ Error loading data: {str(e)}")
        return pd.DataFrame()

def get_default_headers(sheet_name: str) -> List[str]:
    """Get default headers for a sheet type"""
    headers_map = {
        "CustomerDues": ["Date", "Party", "Amount"],
        "PaymentsReceived": ["Date", "Party", "Amount", "Remarks"],
        "GoodsReceived": ["Date", "Supplier", "Particulars", "Amount"],
        "PaymentsToSuppliers": ["Date", "Supplier", "Amount", "Remarks"],
        "Party_Master": ["Name", "Code", "Type", "Contact", "Address"]
    }
    return headers_map.get(sheet_name, ["Date", "Party", "Amount"])

# --- DATA WRITING WITH VALIDATION ---
def append_to_sheet(sheet_name: str, row_data: List) -> Tuple[bool, str]:
    """
    Safely append a row to a sheet with validation
    
    Args:
        sheet_name: Name of the worksheet
        row_data: List of values to append
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        logger.info(f"Appending to {sheet_name}: {row_data}")
        sh = get_sheet_object()
        
        if not sh:
            return False, "Cannot connect to Google Sheets"
        
        worksheet = sh.worksheet(sheet_name)
        worksheet.append_row(row_data)
        
        # Clear cache to show updated data
        st.cache_data.clear()
        
        logger.info(f"Successfully appended to {sheet_name}")
        return True, "Transaction saved successfully"
        
    except gspread.exceptions.APIError as e:
        logger.error(f"API error appending to {sheet_name}: {e}")
        return False, f"API Error: {str(e)}"
    except Exception as e:
        logger.error(f"Error appending to {sheet_name}: {e}")
        logger.error(traceback.format_exc())
        return False, f"Error: {str(e)}"

# --- UTILITY FUNCTIONS ---
def clean_amount(val) -> float:
    """Clean and convert amount string to float"""
    try:
        if not val:
            return 0.0
        # Remove currency symbols and commas
        cleaned = str(val).replace(",", "").replace("₹", "").replace("Rs", "").strip()
        return float(cleaned)
    except (ValueError, TypeError) as e:
        logger.warning(f"Error cleaning amount '{val}': {e}")
        return 0.0

def parse_date(date_str: str) -> Optional[date]:
    """Parse date string to date object"""
    if not date_str:
        return None
    try:
        return pd.to_datetime(date_str, dayfirst=True).date()
    except Exception as e:
        logger.warning(f"Error parsing date '{date_str}': {e}")
        return None

def extract_name_display(display_str: str) -> str:
    """Extract clean name from display string like 'Name (Code)'"""
    if "(" in display_str and ")" in display_str:
        return display_str.split(" (")[0].strip()
    return display_str.strip()

def format_currency(amount: float) -> str:
    """Format amount as currency"""
    return f"₹{amount:,.2f}"

# --- PARTY MANAGEMENT WITH SMART CODING ---
@st.cache_data(ttl=60)
def get_party_master_dict() -> Tuple[Dict[str, str], int, int]:
    """
    Get party master dictionary and max code numbers
    
    Returns:
        Tuple of (party_map: {Name: Code}, max_r: int, max_s: int)
    """
    logger.info("Loading party master dictionary")
    df = fetch_sheet_data("Party_Master")
    party_map = {}
    max_r = 0
    max_s = 0
    
    if not df.empty:
        cols = df.columns.tolist()
        name_col = cols[0] if len(cols) > 0 else None
        code_col = cols[1] if len(cols) > 1 else None
        
        if name_col:
            for _, row in df.iterrows():
                name = str(row[name_col]).strip()
                code = str(row[code_col]).strip() if code_col and row[code_col] else ""
                
                if name:
                    party_map[name] = code
                    # Track max codes
                    if code.startswith("R"):
                        try:
                            max_r = max(max_r, int(code[1:]))
                        except ValueError:
                            pass
                    elif code.startswith("S"):
                        try:
                            max_s = max(max_s, int(code[1:]))
                        except ValueError:
                            pass
    
    logger.info(f"Loaded {len(party_map)} parties (R: {max_r}, S: {max_s})")
    return party_map, max_r, max_s

def generate_new_code(party_type: str, max_r: int, max_s: int) -> str:
    """Generate new party code"""
    if party_type == "Retailer":
        return f"R{max_r + 1}"
    elif party_type == "Supplier":
        return f"S{max_s + 1}"
    return ""

def process_scanned_party(scanned_name: str, party_type: str) -> Tuple[str, str, bool]:
    """
    Process scanned party name with fuzzy matching and code generation
    
    Args:
        scanned_name: Name from scan/input
        party_type: "Retailer" or "Supplier"
        
    Returns:
        Tuple of (display_name, code, needs_update)
    """
    clean_name = scanned_name.strip()
    if not clean_name:
        return "", "", False
    
    party_map, max_r, max_s = get_party_master_dict()
    
    # Fuzzy match against existing names
    existing_names = list(party_map.keys())
    matches = difflib.get_close_matches(clean_name, existing_names, n=1, cutoff=0.7)
    
    if matches:
        matched_name = matches[0]
        code = party_map.get(matched_name, "")
        
        if not code:
            # Existing party without code - generate one
            code = generate_new_code(party_type, max_r, max_s)
            logger.info(f"Generated code {code} for existing party {matched_name}")
            return f"{matched_name} ({code})", code, True
        
        logger.info(f"Matched '{clean_name}' to existing party '{matched_name}' ({code})")
        return f"{matched_name} ({code})", code, False
    else:
        # New party - generate code
        code = generate_new_code(party_type, max_r, max_s)
        logger.info(f"New party '{clean_name}' assigned code {code}")
        return f"{clean_name} ({code})", code, True

def update_party_master_batch(new_entries: List[Tuple[str, str, str]]):
    """
    Add new parties to Party_Master
    
    Args:
        new_entries: List of tuples (Name, Code, Type)
    """
    if not new_entries:
        return
    
    try:
        logger.info(f"Updating party master with {len(new_entries)} entries")
        sh = get_sheet_object()
        if not sh:
            return
        
        ws = sh.worksheet("Party_Master")
        
        # Ensure headers exist
        headers = ws.row_values(1)
        if len(headers) < 3:
            ws.update('A1:E1', [["Name", "Code", "Type", "Contact", "Address"]])
        
        # Append rows
        rows_to_add = [[name, code, ptype, "", ""] for name, code, ptype in new_entries]
        ws.append_rows(rows_to_add)
        
        # Clear cache
        st.cache_data.clear()
        
        logger.info(f"Successfully added {len(rows_to_add)} parties")
        st.toast(f"✅ Auto-assigned codes for {len(rows_to_add)} parties!")
        
    except Exception as e:
        logger.error(f"Error updating party master: {e}")
        st.error(f"Error updating party list: {str(e)}")

def get_all_party_names_display() -> List[str]:
    """Get all party names for dropdown display"""
    party_map, _, _ = get_party_master_dict()
    display_list = []
    
    for name, code in party_map.items():
        if code:
            display_list.append(f"{name} ({code})")
        else:
            display_list.append(name)
    
    return sorted(display_list)

# --- BALANCE CALCULATION ---
@st.cache_data(ttl=30)
def get_party_balances() -> Tuple[Dict[str, float], Dict[str, str]]:
    """
    Calculate balances for all parties
    
    Returns:
        Tuple of (balances: {Party: Balance}, last_dates: {Party: Date})
    """
    logger.info("Calculating party balances")
    
    dues = fetch_sheet_data("CustomerDues")
    payments = fetch_sheet_data("PaymentsReceived")
    goods = fetch_sheet_data("GoodsReceived")
    suppliers_payment = fetch_sheet_data("PaymentsToSuppliers")
    
    balances = {}
    last_dates = {}
    
    # Customer Dues (Debit)
    if not dues.empty:
        for _, row in dues.iterrows():
            party = row.get("Party")
            amount = clean_amount(row.get("Amount"))
            balances[party] = balances.get(party, 0) + amount
            last_dates[party] = row.get("Date")
    
    # Payments Received (Credit)
    if not payments.empty:
        for _, row in payments.iterrows():
            party = row.get("Party")
            amount = clean_amount(row.get("Amount"))
            balances[party] = balances.get(party, 0) - amount
            last_dates[party] = row.get("Date")
    
    # Goods Received (Credit - we owe supplier)
    if not goods.empty:
        for _, row in goods.iterrows():
            party = row.get("Supplier")
            amount = clean_amount(row.get("Amount"))
            balances[party] = balances.get(party, 0) - amount
            last_dates[party] = row.get("Date")
    
    # Payments to Suppliers (Debit - reduces what we owe)
    if not suppliers_payment.empty:
        for _, row in suppliers_payment.iterrows():
            party = row.get("Supplier")
            amount = clean_amount(row.get("Amount"))
            balances[party] = balances.get(party, 0) + amount
            last_dates[party] = row.get("Date")
    
    logger.info(f"Calculated balances for {len(balances)} parties")
    return balances, last_dates

# --- AI/SCAN FUNCTIONS ---
def compress_image(image_file) -> Optional[io.BytesIO]:
    """Compress and prepare image for upload"""
    try:
        logger.info(f"Compressing image: {image_file.type}")
        
        if image_file.type == "application/pdf":
            if not PDF_AVAILABLE:
                logger.error("PDF processing not available")
                st.error("PDF processing not available. Please install PyMuPDF.")
                return None
            
            # Convert PDF to image
            doc = fitz.open(stream=image_file.read(), filetype="pdf")
            images = []
            
            for i in range(doc.page_count):
                page = doc.load_page(i)
                pix = page.get_pixmap(dpi=200)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            
            if not images:
                return None
            
            # Combine pages vertically
            total_height = sum(img.height for img in images)
            max_width = max(img.width for img in images)
            final_img = Image.new('RGB', (max_width, total_height))
            
            y_offset = 0
            for img in images:
                final_img.paste(img, (0, y_offset))
                y_offset += img.height
            
            img = final_img
        else:
            # Regular image
            img = Image.open(image_file)
        
        # Convert to RGB if needed
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # Resize if too large
        max_width = 2500
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
        
        # Save to buffer
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=95, optimize=True)
        output.seek(0)
        
        logger.info("Image compressed successfully")
        return output
        
    except Exception as e:
        logger.error(f"Error compressing image: {e}")
        st.error(f"Error processing image: {str(e)}")
        return None

def upload_to_drive(file_buffer: io.BytesIO, filename: str) -> Optional[str]:
    """Upload file to Google Drive and return shareable link"""
    try:
        if file_buffer is None:
            return None
        
        logger.info(f"Uploading to Drive: {filename}")
        file_buffer.seek(0)
        
        service = get_drive_service()
        if not service:
            return None
        
        # Find or create Gautam_Scans folder
        results = service.files().list(
            q="name='Gautam_Scans' and mimeType='application/vnd.google-apps.folder'",
            fields="files(id)"
        ).execute()
        
        if not results.get('files'):
            # Create folder
            folder_metadata = {
                'name': 'Gautam_Scans',
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(
                body=folder_metadata,
                fields='id'
            ).execute()
            folder_id = folder.get('id')
            logger.info(f"Created folder: {folder_id}")
        else:
            folder_id = results.get('files')[0].get('id')
        
        # Upload file
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(file_buffer, mimetype='image/jpeg', resumable=True)
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        # Make it publicly viewable
        service.permissions().create(
            fileId=file.get('id'),
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        link = file.get('webViewLink')
        logger.info(f"File uploaded: {link}")
        return link
        
    except Exception as e:
        logger.error(f"Error uploading to Drive: {e}")
        st.error(f"Error uploading to Drive: {str(e)}")
        return None

def analyze_image_generic(prompt: str, file_buffer: io.BytesIO) -> Optional[Dict]:
    """Analyze image using OpenAI Vision API"""
    try:
        if file_buffer is None:
            return None
        
        logger.info("Analyzing image with OpenAI")
        file_buffer.seek(0)
        
        # Get API key
        try:
            api_key = st.secrets["OPENAI_API_KEY"]
        except KeyError:
            logger.error("OpenAI API key not found in secrets")
            st.error("OpenAI API key not configured")
            return None
        
        client = OpenAI(api_key=api_key)
        
        # Encode image
        b64_image = base64.b64encode(file_buffer.read()).decode('utf-8')
        
        # Call API
        response = client.chat.completions.create(
            model="gpt-4o",
            temperature=0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}
                    }
                ]
            }]
        )
        
        content = response.choices[0].message.content
        
        # Extract JSON from response
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        
        if json_start >= 0 and json_end > json_start:
            json_str = content[json_start:json_end]
            result = json.loads(json_str)
            logger.info("Image analyzed successfully")
            return result
        else:
            logger.error("No JSON found in response")
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        st.error("Error parsing AI response")
        return None
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")
        st.error(f"Error analyzing image: {str(e)}")
        return None

# --- PDF GENERATION ---
def generate_pdf(party: str, df: pd.DataFrame, start_date: date, end_date: date) -> bytes:
    """Generate PDF statement"""
    try:
        logger.info(f"Generating PDF for {party}")
        
        pdf = FPDF()
        pdf.add_page()
        
        # Header
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(190, 10, "Gautam Pharma", ln=True, align='C')
        
        pdf.set_font("Arial", '', 10)
        pdf.cell(190, 10, f"Statement: {party}", ln=True, align='C')
        pdf.cell(190, 5, f"Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}", ln=True, align='C')
        
        pdf.ln(5)
        
        # Table header
        pdf.set_fill_color(240, 240, 240)
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(25, 8, "Date", 1, 0, 'C', 1)
        pdf.cell(85, 8, "Particulars", 1, 0, 'C', 1)
        pdf.cell(25, 8, "Debit", 1, 0, 'C', 1)
        pdf.cell(25, 8, "Credit", 1, 0, 'C', 1)
        pdf.cell(30, 8, "Balance", 1, 1, 'C', 1)
        
        # Table rows
        pdf.set_font("Arial", '', 9)
        balance = 0
        
        for _, row in df.iterrows():
            date_str = row.get('Date')
            if isinstance(date_str, date):
                date_str = date_str.strftime('%d-%b-%Y')
            
            particulars = str(row.get('Particulars', ''))[:40]
            debit = row.get('Debit', 0)
            credit = row.get('Credit', 0)
            balance += (debit - credit)
            
            pdf.cell(25, 7, str(date_str), 1)
            pdf.cell(85, 7, particulars, 1)
            pdf.cell(25, 7, f"{debit:,.2f}" if debit > 0 else "-", 1)
            pdf.cell(25, 7, f"{credit:,.2f}" if credit > 0 else "-", 1)
            pdf.cell(30, 7, f"{balance:,.2f}", 1, 1)
        
        # Total row
        pdf.set_font("Arial", 'B', 9)
        total_debit = df['Debit'].sum()
        total_credit = df['Credit'].sum()
        
        pdf.cell(110, 7, "TOTAL", 1)
        pdf.cell(25, 7, f"{total_debit:,.2f}", 1)
        pdf.cell(25, 7, f"{total_credit:,.2f}", 1)
        pdf.cell(30, 7, f"{balance:,.2f}", 1, 1)
        
        pdf_bytes = pdf.output(dest='S').encode('latin-1')
        logger.info("PDF generated successfully")
        return pdf_bytes
        
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        raise

# --- SCREEN: HOME ---
def screen_home():
    """Main dashboard screen"""
    st.markdown("### 💊 Gautam Pharma")
    
    try:
        # Get balances
        balances, last_dates = get_party_balances()
        
        # Calculate totals
        total_receivable = sum([v for v in balances.values() if v > 0])
        total_payable = sum([abs(v) for v in balances.values() if v < 0])
        
        # Display summary
        st.markdown(f"""
        <div style="background:white; padding:15px; border-radius:10px; border:1px solid #ddd; 
                    margin-bottom:15px; display:flex; justify-content:space-between;">
            <div style="text-align:center; width:48%; border-right:1px solid #eee;">
                <div style="color:#2e7d32; font-weight:bold; font-size:18px;">₹ {total_receivable:,.0f}</div>
                <div style="color:#555; font-size:12px;">You'll Get</div>
            </div>
            <div style="text-align:center; width:48%;">
                <div style="color:#c62828; font-weight:bold; font-size:18px;">₹ {total_payable:,.0f}</div>
                <div style="color:#555; font-size:12px;">You'll Give</div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Action buttons
        col1, col2, col3, col4 = st.columns(4)
        
        if col1.button("➕\nAdd", use_container_width=True):
            st.session_state.page = 'manual'
            st.rerun()
        
        if col2.button("📅\nDay", use_container_width=True):
            st.session_state.page = 'day_book'
            st.rerun()
        
        if col3.button("📄\nRpt", use_container_width=True):
            st.session_state.page = 'ledger'
            st.rerun()
        
        if col4.button("🎙️\nMic", use_container_width=True):
            st.session_state.page = 'voice'
            st.rerun()
        
        col5, col6, col7, col8 = st.columns(4)
        
        if col5.button("📸\nScan", use_container_width=True):
            st.session_state.page = 'scan_hub'
            st.rerun()
        
        if col6.button("🔔\nRem", use_container_width=True):
            st.session_state.page = 'reminders'
            st.rerun()
        
        if col7.button("⚙️\nTool", use_container_width=True):
            st.session_state.page = 'tools'
            st.rerun()
        
        if col8.button("🔄\nSync", use_container_width=True):
            with st.spinner("Syncing..."):
                st.cache_data.clear()
                logger.info("Cache cleared manually")
            st.success("✅ Synced!")
            time.sleep(0.5)
            st.rerun()
        
        st.markdown("---")
        st.markdown("#### Parties")
        
        # Search
        search_query = st.text_input(
            "Search Party",
            placeholder="Search...",
            label_visibility="collapsed"
        )
        
        # Display parties
        sorted_parties = sorted(balances.items(), key=lambda x: abs(x[1]), reverse=True)
        
        for party, balance in sorted_parties:
            # Skip zero balances
            if abs(balance) < 1:
                continue
            
            # Apply search filter
            if search_query and search_query.lower() not in party.lower():
                continue
            
            # Determine color and status
            color_class = "bal-green" if balance > 0 else "bal-red"
            status_text = "You'll Get" if balance > 0 else "You'll Give"
            last_date = last_dates.get(party, "")
            
            # Display party card
            st.markdown(f"""
            <div class="party-card">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-weight:600; font-size:14px;">{party}</div>
                        <div style="color:#999; font-size:11px;">{last_date}</div>
                    </div>
                    <div>
                        <div class="{color_class}">₹ {abs(balance):,.0f}</div>
                        <div style="color:#999; font-size:11px; text-align:right;">{status_text}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            if st.button(f"View {party}", key=f"view_{party}"):
                st.session_state.selected_party = party
                st.session_state.page = 'ledger'
                st.rerun()
    
    except Exception as e:
        logger.error(f"Error in home screen: {e}")
        st.error(f"Error loading dashboard: {str(e)}")

# --- SCREEN: MANUAL ENTRY ---
def screen_manual():
    """Manual transaction entry screen"""
    st.markdown("### ➕ Add Transaction")
    
    if st.button("⬅ Back"):
        st.session_state.page = 'home'
        st.rerun()
    
    with st.container(border=True):
        # Transaction type
        transaction_type = st.selectbox(
            "Transaction Type",
            [
                "Sale (Bill)",
                "Payment In (Received)",
                "Purchase (In)",
                "Payment Out (Paid)"
            ]
        )
        
        # Date
        transaction_date = st.date_input("Date", date.today())
        
        # Party
        all_parties = get_all_party_names_display()
        if not all_parties:
            st.warning("No parties found. Please add parties in Tools section first.")
            return
        
        party = st.selectbox("Party", all_parties)
        
        # Amount
        amount = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
        
        # Remarks
        remarks = st.text_input("Remarks (Optional)")
        
        # Save button
        if st.button("💾 Save Transaction", type="primary", use_container_width=True):
            # Validate
            transaction = Transaction(
                date=transaction_date,
                party=party,
                amount=amount,
                remarks=remarks,
                transaction_type=transaction_type
            )
            
            errors = transaction.validate()
            
            if errors:
                for error in errors:
                    st.error(f"❌ {error}")
                return
            
            # Determine sheet and row data
            sheet_name = ""
            row_data = []
            
            if "Sale" in transaction_type:
                sheet_name = "CustomerDues"
                row_data = [str(transaction_date), party, amount]
            
            elif "Received" in transaction_type:
                sheet_name = "PaymentsReceived"
                row_data = [str(transaction_date), party, amount, remarks]
            
            elif "Paid" in transaction_type:
                sheet_name = "PaymentsToSuppliers"
                row_data = [str(transaction_date), party, amount, remarks]
            
            elif "Purchase" in transaction_type:
                sheet_name = "GoodsReceived"
                row_data = [str(transaction_date), party, remarks, amount]
            
            # Save
            with st.spinner("Saving..."):
                success, message = append_to_sheet(sheet_name, row_data)
            
            if success:
                st.success(f"✅ {message}")
                logger.info(f"Transaction saved: {transaction_type} - {party} - {amount}")
                time.sleep(1)
                st.session_state.page = 'home'
                st.rerun()
            else:
                st.error(f"❌ {message}")

# --- SCREEN: LEDGER ---
def screen_ledger():
    """Party ledger/statement screen"""
    st.markdown("### 📄 Party Statement")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    # Get all parties
    all_parties = get_all_party_names_display()
    
    if not all_parties:
        st.warning("No parties found in the system. Please add parties first.")
        return
    
    # Handle pre-selected party
    default_index = 0
    if 'selected_party' in st.session_state:
        if st.session_state.selected_party in all_parties:
            default_index = all_parties.index(st.session_state.selected_party)
    
    selected_party = st.selectbox("Select Party", all_parties, index=default_index)
    
    # Quick date range buttons
    col1, col2, col3 = st.columns(3)
    
    if col1.button("This Month"):
        st.session_state['ledger_start'] = date.today().replace(day=1)
        st.session_state['ledger_end'] = date.today()
        st.rerun()
    
    if col2.button("Last Month"):
        last_month_end = date.today().replace(day=1) - timedelta(days=1)
        st.session_state['ledger_start'] = last_month_end.replace(day=1)
        st.session_state['ledger_end'] = last_month_end
        st.rerun()
    
    if col3.button("All Time"):
        st.session_state['ledger_start'] = date(2023, 1, 1)
        st.session_state['ledger_end'] = date.today()
        st.rerun()
    
    # Initialize date range
    if 'ledger_start' not in st.session_state:
        st.session_state['ledger_start'] = date(2025, 1, 1)
    if 'ledger_end' not in st.session_state:
        st.session_state['ledger_end'] = date.today()
    
    # Date inputs
    date_col1, date_col2 = st.columns(2)
    start_date = date_col1.date_input("From", st.session_state['ledger_start'])
    end_date = date_col2.date_input("To", st.session_state['ledger_end'])
    
    # Validate dates
    if start_date > end_date:
        st.error("❌ Start date cannot be after end date")
        return
    
    # Update session state
    st.session_state['ledger_start'] = start_date
    st.session_state['ledger_end'] = end_date
    
    if not selected_party:
        st.info("Please select a party to view their statement")
        return
    
    try:
        # Fetch data
        with st.spinner("Loading transactions..."):
            dues_df = fetch_sheet_data("CustomerDues")
            payments_df = fetch_sheet_data("PaymentsReceived")
            suppliers_payment_df = fetch_sheet_data("PaymentsToSuppliers")
            goods_received_df = fetch_sheet_data("GoodsReceived")
        
        # Build ledger entries
        ledger_entries = []
        
        # Process Customer Dues (Sales - Debit)
        if not dues_df.empty:
            party_dues = dues_df[dues_df["Party"] == selected_party]
            for _, row in party_dues.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date and start_date <= trans_date <= end_date:
                    ledger_entries.append({
                        "Date": trans_date,
                        "Particulars": "Sale",
                        "Debit": clean_amount(row.get("Amount", 0)),
                        "Credit": 0,
                        "Type": "Sale"
                    })
        
        # Process Payments Received (Credit)
        if not payments_df.empty:
            party_payments = payments_df[payments_df["Party"] == selected_party]
            for _, row in party_payments.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date and start_date <= trans_date <= end_date:
                    particulars = str(row.get("Remarks", "Payment Received")).strip()
                    if not particulars:
                        particulars = "Payment Received"
                    ledger_entries.append({
                        "Date": trans_date,
                        "Particulars": particulars,
                        "Debit": 0,
                        "Credit": clean_amount(row.get("Amount", 0)),
                        "Type": "Payment In"
                    })
        
        # Process Supplier Payments (Credit)
        if not suppliers_payment_df.empty:
            supplier_payments = suppliers_payment_df[suppliers_payment_df["Supplier"] == selected_party]
            for _, row in supplier_payments.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date and start_date <= trans_date <= end_date:
                    particulars = str(row.get("Remarks", "Payment Made")).strip()
                    if not particulars:
                        particulars = "Payment Made"
                    ledger_entries.append({
                        "Date": trans_date,
                        "Particulars": particulars,
                        "Debit": 0,
                        "Credit": clean_amount(row.get("Amount", 0)),
                        "Type": "Payment Out"
                    })
        
        # Process Goods Received (Purchase - Debit)
        if not goods_received_df.empty:
            goods_entries = goods_received_df[goods_received_df["Supplier"] == selected_party]
            for _, row in goods_entries.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date and start_date <= trans_date <= end_date:
                    particulars = str(row.get("Particulars", "Purchase")).strip()
                    if not particulars:
                        particulars = "Purchase"
                    ledger_entries.append({
                        "Date": trans_date,
                        "Particulars": particulars,
                        "Debit": clean_amount(row.get("Amount", 0)),
                        "Credit": 0,
                        "Type": "Purchase"
                    })
        
        # Process ledger
        if ledger_entries:
            # Create DataFrame
            ledger_df = pd.DataFrame(ledger_entries)
            ledger_df = ledger_df.sort_values('Date')
            
            # Calculate running balance
            ledger_df['Balance'] = (ledger_df['Debit'] - ledger_df['Credit']).cumsum()
            
            # Calculate totals
            total_debit = ledger_df['Debit'].sum()
            total_credit = ledger_df['Credit'].sum()
            final_balance = ledger_df['Balance'].iloc[-1]
            
            # Display metrics
            col1, col2, col3 = st.columns(3)
            
            col1.metric(
                "Total Debit",
                format_currency(total_debit)
            )
            
            col2.metric(
                "Total Credit",
                format_currency(total_credit)
            )
            
            balance_label = "You'll Get" if final_balance > 0 else "You'll Give"
            col3.metric(
                balance_label,
                format_currency(abs(final_balance)),
                delta=None
            )
            
            st.markdown("---")
            
            # Format for display
            display_df = ledger_df.copy()
            display_df['Date'] = display_df['Date'].apply(lambda x: x.strftime('%d-%b-%Y'))
            display_df['Debit'] = display_df['Debit'].apply(
                lambda x: format_currency(x) if x > 0 else "-"
            )
            display_df['Credit'] = display_df['Credit'].apply(
                lambda x: format_currency(x) if x > 0 else "-"
            )
            display_df['Balance'] = display_df['Balance'].apply(format_currency)
            
            # Display table
            st.dataframe(
                display_df[['Date', 'Particulars', 'Debit', 'Credit', 'Balance']],
                use_container_width=True,
                hide_index=True,
                height=400
            )
            
            st.markdown("---")
            
            # Export options
            col1, col2, col3 = st.columns(3)
            
            # CSV Export
            with col1:
                csv = display_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"{selected_party}_{start_date}_{end_date}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            # PDF Export
            with col2:
                try:
                    pdf_bytes = generate_pdf(selected_party, ledger_df, start_date, end_date)
                    st.download_button(
                        label="📄 Download PDF",
                        data=pdf_bytes,
                        file_name=f"{selected_party}_{start_date}_{end_date}.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
                except Exception as e:
                    st.button("📄 Download PDF", disabled=True, use_container_width=True)
                    st.error(f"PDF generation error: {str(e)}")
            
            # WhatsApp Share
            with col3:
                # Generate message
                message = f"""*Gautam Pharma Statement*
Party: {selected_party}
Period: {start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}

Total Debit: {format_currency(total_debit)}
Total Credit: {format_currency(total_credit)}
Balance: {format_currency(abs(final_balance))} ({balance_label})
"""
                whatsapp_url = f"https://wa.me/?text={urllib.parse.quote(message)}"
                st.link_button(
                    "📱 Share WhatsApp",
                    whatsapp_url,
                    use_container_width=True
                )
        
        else:
            st.info(f"ℹ️ No transactions found for {selected_party} in the selected date range.")
            
            # Suggestions
            with st.expander("💡 Suggestions"):
                st.markdown("- Try expanding the date range")
                st.markdown("- Check if party name is spelled correctly")
                st.markdown("- Verify transactions were recorded")
    
    except Exception as e:
        logger.error(f"Error in ledger screen: {e}")
        logger.error(traceback.format_exc())
        st.error(f"❌ Error loading ledger: {str(e)}")

# --- SCREEN: DAY BOOK ---
def screen_day_book():
    """Day book screen showing all transactions"""
    st.markdown("### 📅 Day Book")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    # Date selection
    selected_date = st.date_input("Select Date", date.today())
    
    try:
        # Fetch all data
        with st.spinner("Loading transactions..."):
            dues_df = fetch_sheet_data("CustomerDues")
            payments_df = fetch_sheet_data("PaymentsReceived")
            suppliers_payment_df = fetch_sheet_data("PaymentsToSuppliers")
            goods_received_df = fetch_sheet_data("GoodsReceived")
        
        all_transactions = []
        
        # Process each type
        if not dues_df.empty:
            for _, row in dues_df.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date == selected_date:
                    all_transactions.append({
                        "Time": row.get("Time", ""),
                        "Type": "Sale",
                        "Party": row.get("Party"),
                        "Amount": clean_amount(row.get("Amount")),
                        "Remarks": ""
                    })
        
        if not payments_df.empty:
            for _, row in payments_df.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date == selected_date:
                    all_transactions.append({
                        "Time": row.get("Time", ""),
                        "Type": "Payment In",
                        "Party": row.get("Party"),
                        "Amount": clean_amount(row.get("Amount")),
                        "Remarks": row.get("Remarks", "")
                    })
        
        if not suppliers_payment_df.empty:
            for _, row in suppliers_payment_df.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date == selected_date:
                    all_transactions.append({
                        "Time": row.get("Time", ""),
                        "Type": "Payment Out",
                        "Party": row.get("Supplier"),
                        "Amount": clean_amount(row.get("Amount")),
                        "Remarks": row.get("Remarks", "")
                    })
        
        if not goods_received_df.empty:
            for _, row in goods_received_df.iterrows():
                trans_date = parse_date(str(row.get("Date", "")))
                if trans_date == selected_date:
                    all_transactions.append({
                        "Time": row.get("Time", ""),
                        "Type": "Purchase",
                        "Party": row.get("Supplier"),
                        "Amount": clean_amount(row.get("Amount")),
                        "Remarks": row.get("Particulars", "")
                    })
        
        if all_transactions:
            df = pd.DataFrame(all_transactions)
            
            # Calculate totals
            total_amount = df['Amount'].sum()
            
            # Display summary
            st.metric("Total Transactions", f"{len(all_transactions)}")
            st.metric("Total Amount", format_currency(total_amount))
            
            st.markdown("---")
            
            # Format display
            df['Amount'] = df['Amount'].apply(format_currency)
            
            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info(f"No transactions found for {selected_date.strftime('%d-%b-%Y')}")
    
    except Exception as e:
        logger.error(f"Error in day book: {e}")
        st.error(f"Error loading day book: {str(e)}")

# --- SCREEN: SCAN HUB ---
def screen_scan_hub():
    """Document scanning hub"""
    st.markdown("### 📸 Scan Documents")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    scan_type = st.radio(
        "What would you like to scan?",
        ["Bill/Invoice", "Payment Receipt", "Purchase Order"],
        horizontal=True
    )
    
    uploaded_file = st.file_uploader(
        "Upload document",
        type=["jpg", "jpeg", "png", "pdf"],
        help="Upload a clear image or PDF of the document"
    )
    
    if uploaded_file:
        # Display preview
        st.image(uploaded_file, caption="Uploaded Document", use_container_width=True)
        
        if st.button("🔍 Analyze Document", type="primary", use_container_width=True):
            with st.spinner("Analyzing document..."):
                # Compress image
                compressed = compress_image(uploaded_file)
                
                if not compressed:
                    st.error("Error processing image")
                    return
                
                # Create prompt based on type
                if scan_type == "Bill/Invoice":
                    prompt = """Extract the following from this bill/invoice:
                    - party_name (customer name)
                    - date (in YYYY-MM-DD format)
                    - total_amount (just the number)
                    - items (list of items if visible)
                    
                    Return ONLY a valid JSON object."""
                
                elif scan_type == "Payment Receipt":
                    prompt = """Extract the following from this payment receipt:
                    - party_name (payer name)
                    - date (in YYYY-MM-DD format)
                    - amount (just the number)
                    - payment_mode (cash/cheque/online)
                    
                    Return ONLY a valid JSON object."""
                
                else:  # Purchase Order
                    prompt = """Extract the following from this purchase order:
                    - supplier_name
                    - date (in YYYY-MM-DD format)
                    - total_amount (just the number)
                    - items (list of items if visible)
                    
                    Return ONLY a valid JSON object."""
                
                # Analyze
                result = analyze_image_generic(prompt, compressed)
                
                if result:
                    st.success("✅ Document analyzed successfully!")
                    
                    # Display extracted data
                    with st.expander("📋 Extracted Data", expanded=True):
                        st.json(result)
                    
                    # Auto-fill form
                    st.markdown("---")
                    st.markdown("#### 📝 Confirm & Save")
                    
                    with st.form("scanned_transaction"):
                        # Determine party type and process
                        if scan_type == "Purchase Order":
                            party_type = "Supplier"
                            party_name = result.get("supplier_name", "")
                        else:
                            party_type = "Retailer"
                            party_name = result.get("party_name", "")
                        
                        # Process party name
                        display_name, code, needs_update = process_scanned_party(
                            party_name,
                            party_type
                        )
                        
                        if needs_update:
                            st.info(f"🆕 New party detected: {display_name}")
                        
                        # Form fields
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            final_date = st.date_input(
                                "Date",
                                value=parse_date(result.get("date", str(date.today()))) or date.today()
                            )
                        
                        with col2:
                            final_amount = st.number_input(
                                "Amount",
                                value=clean_amount(result.get("total_amount") or result.get("amount", 0)),
                                min_value=0.0
                            )
                        
                        final_party = st.text_input("Party Name", value=display_name)
                        remarks = st.text_area("Remarks", value=str(result.get("items", "")))
                        
                        submitted = st.form_submit_button("💾 Save", type="primary", use_container_width=True)
                        
                        if submitted:
                            # Validate
                            trans = Transaction(
                                date=final_date,
                                party=final_party,
                                amount=final_amount,
                                remarks=remarks
                            )
                            
                            errors = trans.validate()
                            
                            if errors:
                                for error in errors:
                                    st.error(f"❌ {error}")
                            else:
                                # Determine sheet
                                if scan_type == "Bill/Invoice":
                                    sheet_name = "CustomerDues"
                                    row_data = [str(final_date), final_party, final_amount]
                                elif scan_type == "Payment Receipt":
                                    sheet_name = "PaymentsReceived"
                                    row_data = [str(final_date), final_party, final_amount, remarks]
                                else:  # Purchase Order
                                    sheet_name = "GoodsReceived"
                                    row_data = [str(final_date), final_party, remarks, final_amount]
                                
                                # Save
                                success, message = append_to_sheet(sheet_name, row_data)
                                
                                if success:
                                    # Update party master if needed
                                    if needs_update:
                                        clean_party_name = extract_name_display(final_party)
                                        update_party_master_batch([(clean_party_name, code, party_type)])
                                    
                                    st.success(f"✅ {message}")
                                    time.sleep(1)
                                    st.session_state.page = 'home'
                                    st.rerun()
                                else:
                                    st.error(f"❌ {message}")
                else:
                    st.error("❌ Could not analyze document. Please try again.")

# --- SCREEN: REMINDERS ---
def screen_reminders():
    """Payment reminders screen"""
    st.markdown("### 🔔 Payment Reminders")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    try:
        # Get overdue parties
        balances, last_dates = get_party_balances()
        
        # Filter for receivables
        receivables = [(party, bal, last_dates.get(party, "")) 
                       for party, bal in balances.items() if bal > 0]
        
        if not receivables:
            st.info("No outstanding receivables")
            return
        
        # Sort by amount
        receivables.sort(key=lambda x: x[1], reverse=True)
        
        st.markdown(f"**{len(receivables)} parties owe you money**")
        st.markdown("---")
        
        for party, amount, last_date in receivables:
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 2])
                
                col1.markdown(f"**{party}**")
                col2.markdown(f"**{format_currency(amount)}**")
                col3.markdown(f"*{last_date}*")
                
                # Send reminder button
                if st.button(f"📱 Send Reminder", key=f"remind_{party}"):
                    message = f"""Hello,

This is a friendly reminder from Gautam Pharma.

Your outstanding balance: {format_currency(amount)}
Last transaction: {last_date}

Please make the payment at your earliest convenience.

Thank you!
Gautam Pharma"""
                    
                    whatsapp_url = f"https://wa.me/?text={urllib.parse.quote(message)}"
                    st.markdown(f"[Open WhatsApp to send reminder]({whatsapp_url})")
                
                st.markdown("---")
    
    except Exception as e:
        logger.error(f"Error in reminders: {e}")
        st.error(f"Error loading reminders: {str(e)}")

# --- SCREEN: VOICE ENTRY ---
def screen_voice():
    """Voice entry screen"""
    st.markdown("### 🎙️ Voice Entry")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    if not VOICE_AVAILABLE:
        st.warning("Voice recording not available. Please install streamlit-mic-recorder.")
        return
    
    st.info("Voice entry feature coming soon!")
    st.markdown("This will allow you to:")
    st.markdown("- Record transactions by voice")
    st.markdown("- Auto-transcribe and extract data")
    st.markdown("- Quick entry on the go")

# --- SCREEN: TOOLS ---
def screen_tools():
    """Settings and tools screen"""
    st.markdown("### ⚙️ Tools & Settings")
    
    if st.button("🏠 Home"):
        st.session_state.page = 'home'
        st.rerun()
    
    tab1, tab2, tab3 = st.tabs(["Party Management", "Data Export", "System"])
    
    with tab1:
        st.markdown("#### Party Management")
        
        # Add new party
        with st.expander("➕ Add New Party"):
            with st.form("add_party"):
                party_name = st.text_input("Party Name")
                party_type = st.selectbox("Type", ["Retailer", "Supplier"])
                contact = st.text_input("Contact Number")
                address = st.text_area("Address")
                
                if st.form_submit_button("Add Party"):
                    if not party_name:
                        st.error("Party name is required")
                    else:
                        # Generate code
                        _, max_r, max_s = get_party_master_dict()
                        code = generate_new_code(party_type, max_r, max_s)
                        
                        # Save
                        success, message = append_to_sheet(
                            "Party_Master",
                            [party_name, code, party_type, contact, address]
                        )
                        
                        if success:
                            st.success(f"✅ Party added with code: {code}")
                            st.cache_data.clear()
                        else:
                            st.error(f"❌ {message}")
        
        # View all parties
        st.markdown("#### All Parties")
        party_df = fetch_sheet_data("Party_Master")
        
        if not party_df.empty:
            st.dataframe(party_df, use_container_width=True, hide_index=True)
        else:
            st.info("No parties found")
    
    with tab2:
        st.markdown("#### Export Data")
        
        # Export all data
        if st.button("📥 Export All Transactions (CSV)"):
            try:
                # Combine all sheets
                all_data = []
                
                for sheet_name in ["CustomerDues", "PaymentsReceived", "GoodsReceived", "PaymentsToSuppliers"]:
                    df = fetch_sheet_data(sheet_name)
                    if not df.empty:
                        df['Type'] = sheet_name
                        all_data.append(df)
                
                if all_data:
                    combined_df = pd.concat(all_data, ignore_index=True)
                    csv = combined_df.to_csv(index=False).encode('utf-8')
                    
                    st.download_button(
                        "Download",
                        csv,
                        f"gautam_pharma_all_transactions_{date.today()}.csv",
                        "text/csv"
                    )
                else:
                    st.info("No data to export")
            
            except Exception as e:
                logger.error(f"Export error: {e}")
                st.error(f"Error exporting: {str(e)}")
    
    with tab3:
        st.markdown("#### System")
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🔄 Clear Cache", use_container_width=True):
                st.cache_data.clear()
                st.cache_resource.clear()
                st.success("✅ Cache cleared")
        
        with col2:
            if st.button("📊 View Logs", use_container_width=True):
                try:
                    with open('gautam_pharma.log', 'r') as f:
                        logs = f.read()
                    st.text_area("Recent Logs", logs, height=300)
                except FileNotFoundError:
                    st.info("No logs available")
        
        st.markdown("---")
        st.markdown("**App Version:** 2.0.0")
        st.markdown("**Last Updated:** February 2026")

# --- MAIN APP ---
def main():
    """Main application entry point"""
    
    # Show splash screen
    show_splash_screen()
    
    # Initialize session state
    if 'page' not in st.session_state:
        st.session_state.page = 'home'
    
    # Route to appropriate screen
    try:
        if st.session_state.page == 'home':
            screen_home()
        elif st.session_state.page == 'manual':
            screen_manual()
        elif st.session_state.page == 'ledger':
            screen_ledger()
        elif st.session_state.page == 'day_book':
            screen_day_book()
        elif st.session_state.page == 'scan_hub':
            screen_scan_hub()
        elif st.session_state.page == 'reminders':
            screen_reminders()
        elif st.session_state.page == 'voice':
            screen_voice()
        elif st.session_state.page == 'tools':
            screen_tools()
        else:
            screen_home()
    
    except Exception as e:
        logger.error(f"Critical error in main app: {e}")
        logger.error(traceback.format_exc())
        st.error("❌ A critical error occurred. Please refresh the page.")
        
        if st.button("🏠 Go Home"):
            st.session_state.page = 'home'
            st.rerun()

if __name__ == "__main__":
    main()
