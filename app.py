import streamlit as st
import json
import io
import time
import pandas as pd
from google import genai
from google.genai import types

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Universal Invoice Agent", page_icon="🏗️", layout="wide")
st.title("🏗️ NHPC Project: Aggregated Invoice Agent")

# Standardized headers derived from Ryker Project Sheets
HARDCODED_FIELDS = [
    "S.No", "Invoice Number", "Invoice Date", "Supplier Name", 
    "Customer Name", "SAP Invoice Number", "Item Description", 
    "HSN Code", "UoM", "Quantity (MT/Nos)", "Rate per Unit (₹)", 
    "Taxable Value (₹)", "GST Rate %", "CGST & SGST Amt (₹)", 
    "IGST Amt (₹)", "Freight Charges (₹)", "Total Invoice Value (₹)"
]

if "all_results" not in st.session_state:
    st.session_state.all_results = []

# --- 2. API KEY SETUP ---
try:
    api_key = st.secrets.get("GEMINI_API_KEY") or st.sidebar.text_input("Gemini API Key", type="password")
except Exception:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# --- 3. FAST NATIVE EXTRACTION ENGINE ---
def extract_data_fast(client, file_bytes, filename):
    models_to_try = ["gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-1.5-flash"]
    
    # Updated prompt with the skip condition built-in
    prompt = f"""
    Act as a professional construction auditor. Examine the attached PDF document carefully.
    
    CRITICAL FILTER RULE:
    Check if the document contains BOTH the phrase "DELIVERY CHALLAN" and "NOT FOR SALE" anywhere on the pages. 
    If BOTH phrases are present, you MUST return an empty JSON array [] as the entire output. Do not extract anything.
    
    Otherwise, extract all individual line items from the attached PDF.
    Target Headers: {HARDCODED_FIELDS}
    
    Mapping Rules:
    - Map synonyms like 'Bill No', 'Voucher', or 'F-Number' to 'Invoice Number'.
    - Map 'Gross Total' or 'Net Amount' to 'Total Invoice Value (₹)'.
    - If billing is inter-state, put tax in 'IGST Amt (₹)'. 
    - If billing is local, put combined tax in 'CGST & SGST Amt (₹)'.
    - Extract the absolute FULL, COMPLETE legal name for 'Supplier Name' and 'Customer Name' exactly as printed on the document header. Do not shorten or omit any words.
    - Capture the COMPLETE item description exactly as written for every line item.
    
    Output: Return a JSON LIST only.
    """
    
    pdf_part = types.Part.from_bytes(data=file_bytes, mime_type="application/pdf")

    for attempt in range(4):
        current_model = models_to_try[min(attempt, len(models_to_try)-1)]
        try:
            response = client.models.generate_content(
                model=current_model, 
                contents=[prompt, pdf_part],
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            
            res_text = ""
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.text:
                        res_text += part.text
            
            clean_json = res_text.strip().replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            return data if isinstance(data, list) else [data]
            
        except Exception as e:
            if "503" in str(e) or "429" in str(e):
                wait = [5, 12, 25][min(attempt, 2)]
                st.warning(f"Server busy. Retrying {filename} with {current_model} in {wait}s...")
                time.sleep(wait)
            else:
                continue
    return []

# --- 4. MAIN INTERFACE ---
if api_key:
    client = genai.Client(api_key=api_key)
    site_location = st.text_input("Project Site Name", "Hyderabad NHPC Site")
    
    col_master, col_new = st.columns(2)
    with col_master:
        existing_master = st.file_uploader("Upload Master Ledger (Optional)", type=["xlsx"])
        if existing_master and not st.session_state.all_results:
            try:
                st.session_state.all_results = pd.read_excel(existing_master).to_dict('records')
                st.success("Existing records loaded.")
            except Exception as e:
                st.error(f"Error: {e}")

    with col_new:
        uploaded_files = st.file_uploader("Upload New Invoice PDFs", type=["pdf"], accept_multiple_files=True)

    if st.button("🚀 Run Aggregated Extraction"):
        if not uploaded_files:
            st.warning("Please upload files first.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            for index, f in enumerate(uploaded_files):
                status_text.text(f"Processing {f.name}...")
                progress_bar.progress((index + 1) / len(uploaded_files))
                
                file_bytes = f.read()
                items = extract_data_fast(client, file_bytes, f.name)
                
                # Check if the AI returned an empty array indicating a skip condition
                if not items:
                    st.info(f"⏭️ Skipped {f.name} (Contains 'NOT FOR SALE' & 'DELIVERY CHALLAN')")
                else:
                    st.session_state.all_results.extend(items)
            
            st.success("Extraction Complete!")

# --- 5. DATA CLEANING & AGGREGATION ---
if st.session_state.all_results:
    st.divider()
    df_raw = pd.DataFrame(st.session_state.all_results)
    
    if not df_raw.empty:
        # A. PRE-CLEANING: Normalize strings and fix numbers before grouping
        text_cols = ["Invoice Number", "Supplier Name", "Item Description", "UoM", "HSN Code", "Customer Name", "Invoice Date"]
        for col in text_cols:
            if col in df_raw.columns:
                df_raw[col] = df_raw[col].astype(str).str.strip().str.upper().replace('NAN', 'N/A')

        numeric_cols = [
            "Quantity (MT/Nos)", "Rate per Unit (₹)", "Taxable Value (₹)", 
            "GST Rate %", "CGST & SGST Amt (₹)", "IGST Amt (₹)", 
            "Freight Charges (₹)", "Total Invoice Value (₹)"
        ]
        for col in numeric_cols:
            if col in df_raw.columns:
                df_raw[col] = pd.to_numeric(
                    df_raw[col].astype(str).str.replace(r'[₹, ]', '', regex=True), 
                    errors='coerce'
                ).fillna(0.0)

        # Early raw de-duplication pass to stop stacking on re-clicks
        df_raw = df_raw.drop_duplicates(
            subset=["Invoice Number", "Supplier Name", "Item Description"], 
            keep="first"
        )

        # B. AGGREGATION LOGIC
        df_final = df_raw.groupby(["Invoice Number", "Supplier Name"], as_index=False).agg({
            "Invoice Date": "first",
            "Customer Name": "first",
            "SAP Invoice Number": "first",
            "HSN Code": "first",
            "UoM": "first",
            "GST Rate %": "first",
            "Item Description": lambda x: f"{len(x)} ITEMS" if len(x) > 1 else x.iloc[0],
            "Quantity (MT/Nos)": "sum",
            "Rate per Unit (₹)": "mean",
            "Taxable Value (₹)": "sum",
            "CGST & SGST Amt (₹)": "sum",
            "IGST Amt (₹)": "sum",
            "Freight Charges (₹)": "sum",
            "Total Invoice Value (₹)": "sum"
        })
        
        # C. SEQUENCE RE-NUMBERING
        df_final = df_final.reset_index(drop=True)
        df_final["S.No"] = (df_final.index + 1).astype(str)

    # D. Final Metadata and Formatting
    df_final["Site Name"] = site_location
    df_final["Timestamp"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    
    final_cols = HARDCODED_FIELDS + ["Site Name", "Timestamp"]
    df_final = df_final.reindex(columns=final_cols)
    
    st.subheader(f"Project Register (Aggregated): {len(df_final)} Records")
    st.dataframe(df_final)

    col_dl, col_clr = st.columns(2)
    with col_dl:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_final.to_excel(writer, index=False)
        st.download_button("📥 Download Aggregated Excel", output.getvalue(), f"{site_location}_Register.xlsx")
    
    with col_clr:
        if st.button("🗑️ Clear All Results"):
            st.session_state.all_results = []; st.rerun()
