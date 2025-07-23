import streamlit as st
import pandas as pd
import time
import io
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import re

# --- SIMPLE AUTH ---
ALLOWED_USERS = {
    "NChotani": "demo_password",
    "user1@example.com": "password123",
    "user2@example.com": "supersecure",
}

def check_user(email, password):
    return email in ALLOWED_USERS and ALLOWED_USERS[email] == password

if "auth" not in st.session_state:
    st.session_state["auth"] = False

st.sidebar.title("Login Required")
user_id = st.sidebar.text_input("User ID (Email or Username)")
user_pass = st.sidebar.text_input("Password", type="password")
login_btn = st.sidebar.button("Login")

if login_btn:
    if check_user(user_id, user_pass):
        st.session_state["auth"] = True
        st.sidebar.success(f"Welcome, {user_id}!")
    else:
        st.session_state["auth"] = False
        st.sidebar.error("Invalid user id or password.")

if not st.session_state["auth"]:
    st.warning("Please log in to use the eBay Bulk Scraper Dashboard.")
    st.stop()

# --- SCRAPER LOGIC ---
def get_item_id(url):
    match = re.search(r'/itm/(\d+)', url)
    if match:
        return match.group(1)
    if url.isdigit() and len(url) >= 12:
        return url
    return url

def build_ebay_url(item):
    if "ebay.com" in item or item.startswith("http"):
        return item
    return f"https://www.ebay.com/itm/{item}"

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(40)
    return driver

def extract_price(driver):
    selectors = [
        "span.ux-textspans",
        "span#prcIsum",
        "span#mm-saleDscPrc",
        "span[itemprop='price']",
        ".x-price-primary span",
        ".display-price",
        ".s-item__price",
    ]
    for sel in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, sel)
        for el in elements:
            txt = el.text.strip()
            if re.search(r'US\s*\$\s*\d', txt):
                return txt
    for el in driver.find_elements(By.TAG_NAME, "span") + driver.find_elements(By.TAG_NAME, "div"):
        txt = el.text.strip()
        if re.search(r'US\s*\$\s*\d', txt):
            return txt
    return "N/A"

def extract_shipping(driver):
    selectors = [
        "span.ux-textspans.ux-textspans--BOLD",
        "span#fshippingCost",
        "span[itemprop='shipping']",
        ".s-item__shipping .display-shipping",
    ]
    for sel in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, sel)
        for el in elements:
            txt = el.text.strip()
            if re.search(r'US\s*\$\s*\d', txt):
                return txt
    for el in driver.find_elements(By.TAG_NAME, "span") + driver.find_elements(By.TAG_NAME, "div"):
        txt = el.text.strip()
        if re.search(r'US\s*\$\s*\d', txt) and "shipping" in txt.lower():
            return txt
    return "N/A"

def extract_inventory(driver):
    selectors = [
        "span.ux-textspans.ux-textspans--SECONDARY",
        "span#qtySubTxt",
        ".d-item-qty-sub-txt",
    ]
    for sel in selectors:
        elements = driver.find_elements(By.CSS_SELECTOR, sel)
        for el in elements:
            txt = el.text.strip()
            if "available" in txt.lower() or "quantity" in txt.lower() or "qty" in txt.lower():
                inv_match = re.search(r'(\d+)', txt.replace(",", ""))
                return inv_match.group(1) if inv_match else txt
    for el in driver.find_elements(By.TAG_NAME, "span") + driver.find_elements(By.TAG_NAME, "div"):
        txt = el.text.strip()
        if "available" in txt.lower() or "quantity" in txt.lower() or "qty" in txt.lower():
            inv_match = re.search(r'(\d+)', txt.replace(",", ""))
            return inv_match.group(1) if inv_match else txt
    return "N/A"

def scrape_ebay(url):
    driver = setup_driver()
    result = {'item id': get_item_id(url), 'price': 'N/A', 'shipping': 'N/A', 'inventory': 'N/A', 'status': 'Failed', 'link': url}
    try:
        driver.get(url)
        time.sleep(2)
        result['price'] = extract_price(driver)
        result['shipping'] = extract_shipping(driver)
        result['inventory'] = extract_inventory(driver)
        if result['price'] != 'N/A' or result['inventory'] != 'N/A':
            result['status'] = 'Success'
        else:
            result['status'] = 'Failed'
    except Exception as e:
        result['status'] = 'Failed'
    finally:
        driver.quit()
    return result

st.title("eBay Bulk Scraper Dashboard")

uploaded_file = st.file_uploader("Upload links.txt or Excel file with links/item numbers", type=['txt', 'xlsx'])

# Read and deduplicate links
links = []
if uploaded_file:
    if uploaded_file.name.endswith('.txt'):
        lines = [line.decode('utf-8').strip() for line in uploaded_file if line.strip()]
        links = list({line for line in lines if line})  # Deduplicate
    elif uploaded_file.name.endswith('.xlsx'):
        df = pd.read_excel(uploaded_file)
        lines = df.iloc[:,0].astype(str).tolist()
        links = list({line.strip() for line in lines if line.strip()})  # Deduplicate

if links:
    st.write(f"Found {len(links)} unique items.")

    # Session state setup
    if 'all_links' not in st.session_state or st.session_state.get('last_uploaded') != uploaded_file:
        st.session_state['all_links'] = links
        st.session_state['last_uploaded'] = uploaded_file
        st.session_state['processed_links'] = set()
        st.session_state['data'] = []
        st.session_state['processing'] = False
        st.session_state['stop_signal'] = False

    processed_links = st.session_state['processed_links']
    data = st.session_state['data']

    # Determine unprocessed links
    all_link_tuples = [(get_item_id(build_ebay_url(l)), build_ebay_url(l)) for l in st.session_state['all_links']]
    unprocessed = [url for (item_id, url) in all_link_tuples if item_id not in processed_links]

    # Dynamic button
    process_btn_label = "Start Processing" if not st.session_state['processing'] else "Stop Processing"
    process_btn = st.button(process_btn_label)
    process_remaining_btn = False

    # Start/Stop logic
    if process_btn:
        if not st.session_state['processing']:
            st.session_state['processing'] = True
            st.session_state['stop_signal'] = False
        else:
            st.session_state['stop_signal'] = True
            st.session_state['processing'] = False

    # Show Process Remaining if stopped and not all links are processed
    if not st.session_state['processing'] and len(processed_links) < len(all_link_tuples) and len(processed_links) > 0:
        process_remaining_btn = st.button("Process Remaining")

    # Main processing loop (runs only if processing and not stopped)
    if st.session_state['processing'] and not st.session_state['stop_signal']:
        progress = st.progress(0)
        count_display = st.empty()
        for idx, url in enumerate(unprocessed):
            if st.session_state['stop_signal']:
                break
            item_id = get_item_id(url)
            if item_id in processed_links:
                continue  # skip already processed
            row = scrape_ebay(url)
            data.append(row)
            processed_links.add(item_id)
            progress.progress((len(processed_links))/len(all_link_tuples))
            count_display.text(f"Processed {len(processed_links)}/{len(all_link_tuples)} links")
        st.session_state['data'] = data
        st.session_state['processing'] = False

    # If Process Remaining is clicked
    if process_remaining_btn:
        st.session_state['processing'] = True
        st.session_state['stop_signal'] = False

    # Show results so far
    if data:
        df = pd.DataFrame(data)
        st.write("Scrape Results (Partial or Complete):")
        st.dataframe(df)
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        st.download_button("Download Results (Excel)", output, "ebay_scrape_results.xlsx")
        st.download_button("Download Results (Text)", df.to_csv(index=False, sep='\t').encode(), "ebay_scrape_results.txt")
        st.write(f"Processed: {len(processed_links)} / {len(all_link_tuples)}")

        # Show Process Remaining if stopped and not all links done
        if not st.session_state['processing'] and len(processed_links) < len(all_link_tuples):
            st.info("You stopped the process. Click 'Process Remaining' to resume scraping on the remaining links.")

else:
    st.info("Please upload a .txt or .xlsx file with eBay links or item numbers.")