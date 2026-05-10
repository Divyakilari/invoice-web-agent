import streamlit as st
import json
import io
import time
import pandas as pd
import pdfplumber
from pdf2image import convert_from_bytes
from google import genai
from google.genai import types

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Universal Invoice Agent", page_icon="🏗️", layout="wide")
st.title("🏗️ Universal AI Invoice Agent")

# Hardcoded fields for the NHPC Project Register
HARDCODED_FIELDS = [
    "S.No", "Invoice Number", "Invoice Date", "Supplier Name", 
    "Customer Name", "SAP Invoice Number", "Item Description", 
    "HSN Code", "UoM", "Quantity (MT/Nos)", "Rate per Unit (₹)", 
    "Taxable Value (₹)", "GST Rate %", "CGST & SGST Amt (₹)", 
    "Freight Charges (₹)", "Total Invoice Value (₹)"
]

if "all_results" not in st.session_state:
    st.session_state.all_results = []

# --- 2. API KEY ---
try:
    api_key = st.secrets["GEMINI_API_KEY"] if "GEMINI_API_KEY" in st.secrets else st.sidebar.text_input("Gemini API Key", type="password")
except:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# --- 3. EXTRACTION ENGINE ---
def extract_data_batch(client, content_list, headers, is_image=False):
    model_id = "gemini-3.1-flash-lite" 
    
    # REFINED PROMPT TO PREVENT TRUNCATION
    prompt = f"""
    Extract invoice data into a JSON LIST using these keys: {headers}.
    
    STRICT INSTRUCTIONS FOR 'Item Description':
    - Capture the COMPLETE description exactly as written.
    - Include all details like Grade (e.g., OPC 43), Packaging (e.g., HDPE BAG), and Quantities/Bags (e.g., Bags:620).
    - DO NOT truncate, summarize, or omit any text from the description line.
    
    If a field is not found, use null. Output MUST be a valid JSON list.
    """
    
    parts = [prompt]
    if is_image:
        for img_bytes in content_list:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
    else:
        parts.append(content_list[0])

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model_id, 
                contents=parts, 
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            clean_json = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception:
            if attempt < 2:
                time.sleep(30)
            else:
                return []
    return []

# --- 4. MAIN INTERFACE ---
if api_key:
    client = genai.Client(api_key=api_key)
    site_location = st.text_input("Project Site Name", "Hyderabad Main Site")
    
    uploaded_files = st.file_uploader(
        "Upload Invoice PDFs or Images", 
        type=["pdf", "jpg", "png", "jpeg"], 
        accept_multiple_files=True
    )

    if st.button("🚀 Run Extraction"):
        if not uploaded_files:
            st.warning("Please upload at least one file.")
        else:
            with st.spinner("AI is analyzing your files. Please wait..."):
                for f in uploaded_files:
                    f_bytes = f.read()
                    
                    if f.name.endswith('.pdf'):
                        with pdfplumber.open(io.BytesIO(f_bytes)) as pdf:
                            text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
                        
                        if len(text.strip()) > 100:
                            items = extract_data_batch(client, [text], HARDCODED_FIELDS)
                            st.session_state.all_results.extend(items)
                        else:
                            images = convert_from_bytes(f_bytes)
                            for i in range(0, len(images), 5):
                                chunk_parts = []
                                for img in images[i:i+5]:
                                    buf = io.BytesIO()
                                    img.save(buf, format='JPEG')
                                    chunk_parts.append(buf.getvalue())
                                items = extract_data_batch(client, chunk_parts, HARDCODED_FIELDS, is_image=True)
                                st.session_state.all_results.extend(items)
                    else:
                        items = extract_data_batch(client, [f_bytes], HARDCODED_FIELDS, is_image=True)
                        st.session_state.all_results.extend(items)
                
                st.success("All files processed successfully!")

# --- 5. RESULTS & DOWNLOAD ---
if st.session_state.all_results:
    st.divider()
    df_final = pd.DataFrame(st.session_state.all_results)
    
    df_final["Site Name"] = site_location
    df_final["Timestamp"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    
    final_cols = HARDCODED_FIELDS + ["Site Name", "Timestamp"]
    df_final = df_final.reindex(columns=final_cols)
    
    st.subheader("Extracted Data Preview")
    # Using use_container_width to see full descriptions more easily
    st.dataframe(df_final, use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_final.to_excel(writer, index=False)
        st.download_button(
            label="📥 Download Data (Excel)",
            data=output.getvalue(),
            file_name="Extracted_Invoices.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    with col2:
        if st.button("🗑️ Clear Results"):
            st.session_state.all_results = []
            st.rerun()
