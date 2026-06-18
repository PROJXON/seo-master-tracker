import os
import requests
import pandas as pd
import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest
from datetime import datetime, timedelta

# --- SECURE IDS AND KEYS (Pulled from GitHub Secrets) ---
SHEET_ID = os.environ.get("SHEET_ID")
PAGESPEED_API_KEY = os.environ.get("PAGESPEED_API_KEY")
OPENPAGERANK_API_KEY = os.environ.get("OPENPAGERANK_API_KEY")

def get_google_clients():
    print("Authenticating with Google Cloud...")
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/webmasters.readonly',
        'https://www.googleapis.com/auth/analytics.readonly'
    ]
    
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
    webmasters_service = build('searchconsole', 'v1', credentials=creds)
    ga_client = BetaAnalyticsDataClient(credentials=creds)
    
    return gc, webmasters_service, ga_client

def get_config(gc):
    print("Downloading Configuration Data...")
    sheet = gc.open_by_key(SHEET_ID).worksheet("Websites")
    df = pd.DataFrame(sheet.get_all_records())
    
    valid_trues = [True, 'TRUE', 'True', 'true']
    active_sites = df[df['Active'].isin(valid_trues)]
    
    sites_dict = {}
    for index, row in active_sites.iterrows():
        domain = row['Domain']
        raw_keywords = str(row.get('Target_Keywords', ''))
        
        sites_dict[domain] = {
            'keywords': [k.strip().lower() for k in raw_keywords.split(',') if k.strip()],
            'ga4_id': str(row.get('GA4_Property_ID', '')).strip(),
            'check_seo': row.get('Check_SEO') in valid_trues,
            'check_speed': row.get('Check_Speed') in valid_trues,
            'check_backlinks': row.get('Check_Backlinks') in valid_trues 
        }
        
    return sites_dict

def clean_cell_value(val):
    val_str = str(val).strip()
    if not val_str:
        return ""
    
    # Try converting to integer
    try:
        if val_str.isdigit() or (val_str.startswith('-') and val_str[1:].isdigit()):
            return int(val_str)
    except ValueError:
        pass
        
    # Try converting to float
    try:
        return float(val_str)
    except ValueError:
        pass
        
    return val

def upsert_to_sheet(sheet, new_rows, key_indices):
    if not new_rows:
        return
        
    all_values = sheet.get_all_values()
    if not all_values:
        cleaned_new = [[clean_cell_value(cell) for cell in row] for row in new_rows]
        sheet.append_rows(cleaned_new, value_input_option='USER_ENTERED')
        return
        
    headers = all_values[0]
    existing_data = all_values[1:]
    
    new_keys = set(tuple(str(row[i]).strip() for i in key_indices) for row in new_rows)
    
    filtered_data = []
    for row in existing_data:
        padded_row = row + [''] * (max(key_indices) + 1 - len(row))
        row_key = tuple(str(padded_row[i]).strip() for i in key_indices)
        
        if row_key not in new_keys:
            filtered_data.append(row)
            
    combined_rows = filtered_data + new_rows
    
    cleaned_dataset = [headers]
    for row in combined_rows:
        cleaned_dataset.append([clean_cell_value(cell) for cell in row])
            
    sheet.clear()
    sheet.update(range_name='A1', values=cleaned_dataset, value_input_option='USER_ENTERED')

def run_gsc_tracker(webmasters_service, domain, target_keywords, results_sheet):
    site_url = f"sc-domain:{domain.replace('www.', '').replace('https://', '').replace('http://', '')}"
    print(f"\n[GSC] Fetching keywords for {site_url}")
    target_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    
    try:
        response = webmasters_service.searchanalytics().query(
            siteUrl=site_url, 
            body={'startDate': target_date, 'endDate': target_date, 'dimensions': ['query'], 'rowLimit': 2000}
        ).execute()
        
        rows = response.get('rows', [])
        new_rows = []
        for row in rows:
            keyword = row['keys'][0].lower()
            if target_keywords and keyword not in target_keywords:
                continue
            new_rows.append([
                target_date, domain, keyword, round(row.get('position', 0), 1), 
                row.get('impressions', 0), row.get('clicks', 0), round(row.get('ctr', 0) * 100, 2)
            ])
            
        if new_rows:
            upsert_to_sheet(results_sheet, new_rows, key_indices=[0, 1, 2])
            print(" -> Processed target keywords successfully.")
        else:
            print(" -> None of your target keywords ranked today.")
    except Exception as e:
        print(" -> GSC Error occurred.")

def run_ga4_tracker(ga_client, domain, property_id, results_sheet):
    if not property_id: return
    print(f"\n[GA4] Fetching traffic for {domain}")
    target_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    
    try:
        response = ga_client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date="2daysAgo", end_date="2daysAgo")],
            metrics=[Metric(name="sessions"), Metric(name="engagementRate"), Metric(name="bounceRate")]
        ))
        if response.rows:
            row = response.rows[0]
            sessions = int(row.metric_values[0].value)
            eng_rate = round(float(row.metric_values[1].value), 4)
            balance = round(float(row.metric_values[2].value), 4)
            
            new_row = [target_date, domain, sessions, eng_rate, balance]
            upsert_to_sheet(results_sheet, [new_row], key_indices=[0, 1])
            print(" -> Saved GA4 data successfully.")
        else:
            print(" -> No traffic data found.")
    except Exception as e:
        print(" -> GA4 Error occurred.")

def run_pagespeed_tracker(domain, results_sheet):
    print(f"\n[PageSpeed] Testing Core Web Vitals for {domain}")
    url = domain if domain.startswith('http') else f"https://{domain}"
    target_date = datetime.now().strftime("%Y-%m-%d")
    
    try:
        response = requests.get(f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={url}&key={PAGESPEED_API_KEY}&strategy=desktop").json()
        if 'lighthouseResult' in response:
            score = round(response['lighthouseResult']['categories']['performance']['score'] * 100)
            lcp_raw = response['lighthouseResult']['audits']['largest-contentful-paint']['numericValue']
            cls_raw = response['lighthouseResult']['audits']['cumulative-layout-shift']['numericValue']
            
            lcp = round(lcp_raw / 1000, 2) 
            cls = round(cls_raw, 3)
            
            new_row = [target_date, domain, score, lcp, cls]
            upsert_to_sheet(results_sheet, [new_row], key_indices=[0, 1])
            print(" -> Saved PageSpeed Core Web Vitals data successfully.")
    except Exception as e:
        print(" -> PageSpeed Error occurred.")

def run_backlink_tracker(domain, results_sheet):
    print(f"\n[OpenPageRank] Checking Domain Authority for {domain}")
    target_date = datetime.now().strftime("%Y-%m-%d")
    clean_domain = domain.replace('www.', '').replace('https://', '').replace('http://', '').strip('/')
    
    headers = {'API-OPR': OPENPAGERANK_API_KEY}
    url = f"https://openpagerank.com/api/v1.0/getPageRank?domains[]={clean_domain}"
    
    try:
        response = requests.get(url, headers=headers).json()
        if response.get('status_code') == 200 and response.get('response'):
            data = response['response'][0]
            score = data.get('page_rank_decimal', 0)
            raw_rank = data.get('rank', 0)
            rank = int(raw_rank) if str(raw_rank).isdigit() else 0
            
            new_row = [target_date, domain, score, rank]
            upsert_to_sheet(results_sheet, [new_row], key_indices=[0, 1])
            print(" -> Saved Domain Authority data successfully.")
        else:
            print(" -> Error: Could not fetch OpenPageRank data.")
    except Exception as e:
        print(" -> Backlink API Error occurred.")

def main():
    print("--- Starting Master Data Tracker ---")
    gc, webmasters_service, ga_client = get_google_clients()
    sites_dict = get_config(gc)
    
    seo_sheet = gc.open_by_key(SHEET_ID).worksheet("SEO_Rankings")
    ga4_sheet = gc.open_by_key(SHEET_ID).worksheet("GA4_Metrics")
    cwv_sheet = gc.open_by_key(SHEET_ID).worksheet("CWV_Metrics")
    backlink_sheet = gc.open_by_key(SHEET_ID).worksheet("Backlink_Metrics")
    
    for domain, config in sites_dict.items():
        if config['check_seo']:
            run_gsc_tracker(webmasters_service, domain, config['keywords'], seo_sheet)
        if config['ga4_id']:
            run_ga4_tracker(ga_client, domain, config['ga4_id'], ga4_sheet)
        if config['check_speed']:
            run_pagespeed_tracker(domain, cwv_sheet)
        if config['check_backlinks']:
            run_backlink_tracker(domain, backlink_sheet)
            
    print("\n--- Master Tracking Complete ---")

if __name__ == "__main__":
    main()