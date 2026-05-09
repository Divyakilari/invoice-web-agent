import streamlit as st
import json
import io
import time
import pandas as pd
import pdfplumber
from pdf2image import convert_from_bytes
from google import genai
from google.genai import types
from streamlit_gsheets import GSheetsConnection

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="Universal Invoice Agent", page_icon="🏗️", layout="wide")

st.title("🏗️ Universal AI Invoice Agent")
st.markdown("""
**How to use:**
1. **Office Setup:** (Optional) Upload an Excel file in the sidebar to set your custom columns.
2. **Input Data:** Use **Office Mode** for existing PDFs/Images or **Site Mode** for phone photos.
3. **Sync:** Review the data and click **'Send to Office'** to update the central ledger.
""")

# --- 2. SECRETS & API KEY (Cloud & Local Support) ---
try:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    else:
        api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")
except Exception:
    api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

# --- 3. TEMPLATE LOGIC (The Brain) ---
st.sidebar.header("1. Setup Your Ledger")
template_file = st.sidebar.file_uploader("Upload Master Excel Template (Optional)", type=["xlsx", "csv"])

# Default headers used if no Excel is provided
default_headers = [
    "Invoice Number", "Invoice Date", "Supplier Name", 
    "Item Description", "Quantity", "Total Amount"
]

if template_file:
    if template_file.name.endswith('.csv'):
        df_temp = pd.read_csv(template_file)
    else:
        df_temp = pd.read_excel(template_file)
    target_headers = df_temp.columns.tolist()
    st.sidebar.success(f"Using Custom Template: {len(target_headers)} fields")
else:
    target_headers = default_headers
    st.sidebar.info("No template. Using Default Headers.")

# Auto-add tracking fields for your central database
if "Site Name" not in target_headers: target_headers.append("Site Name")
if "Timestamp" not in target_headers: target_headers.append("Timestamp")

# --- 4. BATCH EXTRACTION ENGINE ---
def extract_data_batch(client, content_list, headers, is_image=False):
    """Sends multiple pages in ONE request to save quota and speed up processing."""
    model_id = "gemini-3.1-flash-lite"
    prompt = f"""
    Extract ALL invoice line items from ALL provided pages into a single JSON LIST.
    Use these exact keys: {headers}. 
    If a field is missing on a page, use null.
    """
    
    parts = [prompt]
    if is_image:
        for img_bytes in content_list:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
    else:
        parts.append(content_list[0]) # Single text block for digital PDFs

    # Retry logic for 429 Resource Exhausted errors
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model_id, 
                contents=parts,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            clean_json = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                st.warning(f"AI is busy. Resting 30s before retry (Attempt {attempt+1}/3)...")
                time.sleep(30)
            else:
                st.error(f"Extraction failed: {e}")
                return []

# --- 5. MAIN INTERFACE ---
if not api_key:
    st.warning("Please provide an API Key in the sidebar or secrets to begin.")
else:
    client = genai.Client(api_key=api_key)
    all_results = []
    
    site_location = st.text_input("Project Site Name", "Hyderabad Main Site")
    tab1, tab2 = st.tabs(["📁 Office Mode (Bulk Upload)", "📸 Site Mode (Camera Snap)"])

    # --- TAB 1: BULK PROCESSING ---
    with tab1:
        uploaded_files = st.file_uploader("Upload PDFs or Images", type=["pdf", "jpg", "jpeg", "png"], accept_multiple_files=True)
        if st.button("🚀 Process All Files"):
            for f in uploaded_files:
                st.info(f"Processing {f.name}...")
                f_bytes = f.read()
                
                if f.type == "application/pdf":
                    with pdfplumber.open(io.BytesIO(f_bytes)) as pdf:
                        text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
                    
                    if len(text.strip()) > 100:
                        # Digital PDF: Process as text batch
                        items = extract_data_batch(client, [text], target_headers)
                        all_results.extend(items)
                    else:
                        # Scanned PDF: Process as image batch
                        st.write("📸 Scanned PDF detected. Converting pages to images...")
                        images = convert_from_bytes(f_bytes)
                        image_parts = []
                        for img in images:
                            buf = io.BytesIO()
                            img.save(buf, format='JPEG')
                            image_parts.append(buf.getvalue())
                        
                        # Process all pages at once (Efficient)
                        items = extract_data_batch(client, image_parts, target_headers, is_image=True)
                        all_results.extend(items)
                else:
                    # Single Image Upload
                    items = extract_data_batch(client, [f_bytes], target_headers, is_image=True)
                    all_results.extend(items)

    # --- TAB 2: CAMERA CAPTURE ---
    with tab2:
        cam_image = st.camera_input("Snap a photo of the invoice/receipt")
        if cam_image and st.button("✨ Extract from Photo"):
            with st.spinner("AI is reading the photo..."):
                items = extract_data_batch(client, [cam_image.getvalue()], target_headers, is_image=True)
                all_results.extend(items)

    # --- 6. RESULTS & CLOUD SYNC ---
    if all_results:
        st.divider()
        df_final = pd.DataFrame(all_results)
        
        # Add metadata for tracking
        df_final["Site Name"] = site_location
        df_final["Timestamp"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
        
        # Ensure only target headers are shown in correct order
        df_final = df_final.reindex(columns=target_headers)
        
        st.subheader("Extracted Data Preview")
        st.dataframe(df_final)

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("✅ Send to Office Master Ledger"):
                try:
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    # Worksheet must be named 'Sheet1'
                    existing = conn.read(worksheet="Sheet1")
                    updated = pd.concat([existing, df_final], ignore_index=True)
                    conn.update(worksheet="Sheet1", data=updated)
                    st.success("Successfully updated the Office Master Ledger!")
                except Exception as e:
                    st.error(f"GSheets Sync Failed: {e}. Check your Secrets settings.")

        with col_b:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_final.to_excel(writer, index=False)
            st.download_button("📥 Download Excel Locally", output.getvalue(), "Invoice_Data.xlsx")
