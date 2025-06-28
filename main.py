import re

import requests

import time
import gspread
from gspread.utils import ValueInputOption
from oauth2client.service_account import ServiceAccountCredentials


scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name('service-account.json', scope)
client = gspread.authorize(creds)
sheet = client.open('Template_CS_2_cases').get_worksheet(0)



def update_sheet():
    """Updates column C (Now price) for all rows with hash_name."""
    hashnames = sheet.col_values(1)[1:]
    for idx, hashname in enumerate(hashnames, start=2):
        if not hashname:
            continue
        try:
            data = safe_item_data(hashname)
            price = data.get("buy_req")
            if price is not None:
                sheet.update_cell(idx, 3, round(price, 2))
                print(f"Row {idx}: {hashname} → {price:.2f}")
        except Exception as e:
            print(f"Error for {hashname} (row {idx}): {e}")
        time.sleep(1)
    print("Price update completed")

def write_case_names_to_sheet(tracked_cases: list):
    existing = set(sheet.col_values(1)[1:])
    new_cases = [hn for hn in tracked_cases if hn not in existing]
    if not new_cases:
        print("No new cases to add.")
        return
    rows = []
    for hn in new_cases:
        rows.append([hn])

    sheet.append_rows(rows,table_range='A1', value_input_option=ValueInputOption.user_entered)
    print(f"Added {len(new_cases)} cases.")


def get_hashname(user_input: str) -> str:
    """Gets the exact hash_name from Steam Market search."""
    url = f"https://steamcommunity.com/market/search/render/?query={user_input}&appid=730&start=0&count=100&norender=1"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("results", [])
    if not data:
        raise ValueError(
            f"No cases found for query '{user_input}'. "
            "Please check the spelling and try again."
        )
    json = resp.json()
    return json["results"][0]["hash_name"]

def get_nameid(hashname: str) -> int:
    """Extracts item_nameid from Steam Market listing page."""
    url  = f"https://steamcommunity.com/market/listings/730/{hashname}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    html = resp.text
    m = re.findall(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)", html)
    if not m:
        raise RuntimeError(
            "Could not find item_nameid on the listing page. "
            "The item might be incorrectly specified or page structure has changed."
        )
    return int(m[0])

def item_data(hashname) -> dict:
    """Fetches current market data for an item."""
    nameid = str(get_nameid(hashname))
    out = {}
    order_data = (requests.get(f"https://steamcommunity.com/market/itemordershistogram?country=UA&currency=18&language=ukrainian&two_factor=0&item_nameid={nameid}").text)
    out["buy_req"] = int((order_data.split('\"highest_buy_order":\"')[1]).split('\"')[0])/100
    out["sell_req"] = int((order_data.split('\"lowest_sell_order":\"')[1]).split('\"')[0])/100
    try:
        out["volume"] = int(((requests.get(f"https://steamcommunity.com/market/priceoverview/?appid=730&currency=1&market_hash_name={hashname}").text).split('volume\":"')[1]).split('\"')[0])
    except:
        pass
    out["nameid"] = nameid
    return out

def safe_item_data(hashname: str, max_retries: int = 5) -> dict:
    """Calls item_data with exponential backoff for 429 errors."""
    delay = 1
    for attempt in range(max_retries):
        try:
            return item_data(hashname)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"Rate limited for {hashname}, waiting {delay} seconds...")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Max retries exceeded for {hashname}")


if __name__ == '__main__':
    try:
        while True:
            print("\nChoose an option:")
            print("1. Add new cases into the table")
            print("2. Start tracking current prices")
            print("3. Exit")
            choice = input("Enter (1–3): ").strip()

            if choice == "1":
                # Блок «Добавить кейсы»
                to_track = set()
                print("Enter case name with word 'case' to track\nfor example: 'breakout case'")
                while True:
                    user_input = input("Enter case name (to exit type 'exit'): ").strip()
                    if user_input.lower() == "exit":
                        break
                    try:
                        to_show = get_hashname(user_input)
                    except ValueError as e:
                        print(e)
                        continue

                    print(to_show)
                    yn = input("Is this correct? (y/n): ").strip().lower()
                    if yn == "y":
                        to_track.add(to_show)

                if to_track:
                    write_case_names_to_sheet(list(to_track))
                else:
                    print("No new cases to track. ")

            elif choice == "2":
                interval = 300
                print(f"Launching update every {interval:.0f} sec. (use Ctrl+C to stop)")
                try:
                    while True:
                        update_sheet()
                        time.sleep(interval)
                except KeyboardInterrupt:
                    print("\nTracking was stopped by user. ")

            elif choice == "3":
                print("Exiting. Goodbye!")
                break

            else:
                print("Invalid choice. Please try again(1-3).")

    except Exception as e:
        print("Error: ", e)
