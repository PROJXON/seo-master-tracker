import os
import requests
import gspread
import pandas as pd
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import cloudscraper
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# --- SECURE ID (Pulled from GitHub Secrets) ---
SHEET_ID = os.environ.get("SHEET_ID")

def get_google_client():
    print("Authenticating via Custom Desktop App...")
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', scopes)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', scopes)
            creds = flow.run_local_server(port=0)
            
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    gc = gspread.authorize(creds)
    return gc

def get_websites_to_audit(gc):
    print("Downloading spreadsheet data...")
    sheet = gc.open_by_key(SHEET_ID).worksheet("Websites")
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    valid_trues = [True, 'TRUE', 'True', 'true']
    active_sites = df[
        (df['Active'].isin(valid_trues)) & 
        (df['Check_Links'].isin(valid_trues))
    ]
    return active_sites['Domain'].tolist()

def audit_website(domain):
    if not domain.startswith('http'):
        domain = 'https://' + domain
        
    print(f"\n[Scanning] {domain}")
    
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    
    try:
        response = scraper.get(domain, timeout=15)
        response.raise_for_status() 
        
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.find_all('a')
        
        broken_links = []
        checked_urls = set() 
        
        for link in links:
            href = link.get('href')
            if not href or href.startswith(('mailto:', 'tel:', '#', 'javascript:')):
                continue
                
            full_url = urljoin(domain, href)
            if full_url in checked_urls:
                continue
                
            checked_urls.add(full_url)
            
            try:
                link_response = scraper.get(full_url, timeout=10, stream=True)
                if link_response.status_code >= 400 and link_response.status_code != 403:
                    broken_links.append(full_url)
            except Exception:
                broken_links.append(full_url)

        return len(checked_urls), broken_links

    except Exception as e:
        print(" -> CRITICAL ERROR during scan.")
        return 0, []

def run_auditor():
    print("--- Starting Daily SEO Auditor ---")
    
    print("Authenticating with Google Cloud...")
    gc = get_google_client()
    
    domains = get_websites_to_audit(gc)
    
    if not domains:
        print("No active domains found to audit. Exiting.")
        return

    print(f"\nFound {len(domains)} active websites to audit.")
    
    results_sheet = gc.open_by_key(SHEET_ID).worksheet("Daily_Results")
    
    for domain in domains:
        total_scanned, broken_links = audit_website(domain)
        
        today_date = datetime.now().strftime("%Y-%m-%d")
        broken_count = len(broken_links)
        broken_urls_text = ", ".join(broken_links) if broken_links else "None"
        
        new_row = [today_date, domain, total_scanned, broken_count, broken_urls_text]
        results_sheet.append_row(new_row)
        
        print(f" -> Saved audit results for {domain} to Google Sheets.")
        
    print("\n--- Audit Complete ---")

if __name__ == "__main__":
    run_auditor()