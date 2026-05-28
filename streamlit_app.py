import streamlit as st
import requests
import re
import time
from datetime import datetime, timedelta, timezone
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 定数設定 ---
SPREADSHEET_ID = "1HwNBcYJUSofFS-HkQI9eVLZWnuOJaXPzMmE8nC6E_bY"
SHEET_NAME = "カード"
JST = timezone(timedelta(hours=+9), 'JST') # 日本時間設定
SPOOFED_HEADERS = {"User-Agent": "Mozilla/5.0"}
RARITY_LIST = ["SAR","SR","AR","CHR","CSR","UR","HR","RRR","RR","R","C","U","P","PROMO","MUR","MA"]

# --- スプレッドシート接続 ---
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    service_account_info = dict(st.secrets["gcp_service_account"])
    # 鍵のクリーニング
    if "private_key" in service_account_info:
        service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

# --- メイン処理 ---
st.title("🃏 ポケカ価格自動反映ツール（修正版）")

if st.button("🔄 価格更新を開始"):
    sheet = get_sheet()
    existing_rows = sheet.get_all_values()
    pokemon_map = {f"{row[0]}_{row[1]}_{row[2]}": {"row_num": idx, "price": row[4]} 
                   for idx, row in enumerate(existing_rows[1:], start=2) if len(row) >= 3}

    for current_page in range(1, 6):
        url = f"https://snkrdunk.com/search?brandIds=pokemon&sort=hottest&page={current_page}"
        res = requests.get(url, headers=SPOOFED_HEADERS)
        matches = re.findall(r'<a[^>]*href="([^"]+?\/used)"[^>]*aria-label="([^"]+?)"', res.text)

        for used_path, full_title in matches:
            # 名前・型番抽出（既存ロジック）
            before_bracket = full_title.split("[")[0].strip()
            card_no = re.search(r'\[([^\]]+)\]', full_title).group(1) if "[" in full_title else ""
            rarity = next((r for r in RARITY_LIST if before_bracket.endswith(r)), "")
            name = before_bracket.replace(rarity, "").strip()

            # --- PSA10価格抽出（厳格版） ---
            psa10_prices = []
            detail_res = requests.get(f"https://snkrdunk.com{used_path}", headers=SPOOFED_HEADERS)
            history_blocks = re.findall(r'<li class="used">(.*?)</li>', detail_res.text, re.DOTALL)
            
            for block in history_blocks:
                # sizeが「PSA10」と完全に一致するもののみ対象
                if '<p class="size">PSA10</p>' in block:
                    price_match = re.search(r'<p class="price">¥\s*([\d,]+)</p>', block)
                    if price_match:
                        psa10_prices.append(int(price_match.group(1).replace(",", "")))
                if len(psa10_prices) >= 6: break
            
            # 平均計算または「なし」
            psa_avg = round(sum(psa10_prices) / len(psa10_prices)) if psa10_prices else "なし"
            now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

            # スプレッドシート更新
            key = f"{name}_{rarity}_{card_no}"
            if key in pokemon_map:
                sheet.update_cell(pokemon_map[key]["row_num"], 5, str(psa_avg))
                sheet.update_cell(pokemon_map[key]["row_num"], 6, now_str)
            
            time.sleep(2)

    st.success("処理が完了しました")
