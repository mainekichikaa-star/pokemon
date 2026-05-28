import streamlit as st
import pandas as pd
import requests
import re
import time
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

# --- 定数設定 ---
SPREADSHEET_ID = "1HwNBcYJUSofFS-HkQI9eVLZWnuOJaXPzMmE8nC6E_bY"
SHEET_NAME = "カード"

HEADERS = ["名前", "レアリティ", "型番（カード番号）", "収録パック", "現在の価格(PSA10中央値)", "最終更新", "URL"]
SPOOFED_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
}
RARITY_LIST = ["SAR","SR","AR","CHR","CSR","UR","HR","RRR","RR","R","C","U","P","PROMO","MUR","MA"]

# --- スプレッドシート接続関数（Secrets対応版） ---
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # StreamlitのSecrets（管理画面から設定する隠し環境変数）から認証情報を取得
    try:
        service_account_info = json.loads(st.secrets["gcp_service_account"])
    except Exception as e:
        st.error("StreamlitのSecretsに 'gcp_service_account' が設定されていないか、JSONの形式が正しくありません。")
        st.stop()
        
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client = gspread.authorize(creds)
    workbook = client.open_by_key(SPREADSHEET_ID)
    try:
        return workbook.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        sheet = workbook.add_worksheet(title=SHEET_NAME, rows="100", cols="20")
        sheet.append_row(HEADERS)
        return sheet

# --- Streamlit 画面構成 ---
st.set_page_config(page_title="ポケカ価格自動反映ツール", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（スニダン連携）")
st.write("ボタンを押すとスニダンのPSA10相場をスクレイピングし、指定のスプレッドシートを自動更新します。")

# サイドバーに設定表示
st.sidebar.header("現在の設定")
st.sidebar.info(f"対象シート: {SHEET_NAME}\n\nID: {SPREADSHEET_ID[:15]}...")

# 実行ボタン
if st.button("🔄 価格更新を開始する", type="primary"):
    try:
        sheet = get_sheet()
        st.success("Googleスプレッドシートへの接続に成功しました！")
    except Exception as e:
        st.error(f"スプレッドシートへの接続エラー: {e}")
        st.stop()

    # 既存データの読み込みとマッピング
    existing_rows = sheet.get_all_values()
    pokemon_map = {}
    if len(existing_rows) > 0 and existing_rows[0][0] == "名前":
        for idx, row in enumerate(existing_rows[1:], start=2): # 1行目はヘッダーなので2行目から
            if len(row) >= 3:
                key = f"{row[0]}_{row[1]}_{row[2]}"
                pokemon_map[key] = {"row_num": idx, "price": row[4] if len(row) > 4 else ""}

    # ログ出力用のプレースホルダー
    log_area = st.empty()
    progress_bar = st.progress(0)
    
    current_page = 1
    max_pages = 5 # 負荷を考慮し、まずは5ページ制限
    
    all_success_count = 0

    while current_page <= max_pages:
        log_area.markdown(f"### 📄 一覧ページ {current_page} を解析中...")
        progress_bar.progress(current_page / max_pages)

        url = f"https://snkrdunk.com/search?keywords=%E3%83%88%E3%83%AC%E3%82%AB+%28%E3%82%B7%E3%83%B3%E3%82%B0%E3%83%AB%E3%82%AB%E3%83%BC%E3%83%89%29&searchCategoryIds=6%2F33&brandIds=pokemon&sort=hottest&page={current_page}"
        
        try:
            res = requests.get(url, headers=SPOOFED_HEADERS, timeout=10)
            time.sleep(2)
        except Exception as e:
            st.warning(f"通信エラー（ページ {current_page}）: {e}")
            time.sleep(5)
            continue

        if res.status_code != 200:
            st.error(f"スニダンへのアクセスに失敗しました。ステータスコード: {res.status_code}")
            break

        html = res.text
        product_regex = r'<a[^>]*href="([^"]+?\/apparels\/[^"]+?\/used)"[^>]*aria-label="([^"]+?) - ¥([\d,]+)"'
        matches = re.findall(product_regex, html)

        if not matches:
            fallback_regex = r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ¥([\d,]+)"'
            fallback_matches = re.findall(fallback_regex, html)
            for f_match in fallback_matches:
                path = f_match[0].split('?')[0]
                if "/products/" in path:
                    path = path.replace("/products/", "/apparels/")
                if "/used" not in path:
                    path = path + "/used"
                matches.append((path, f_match[1], f_match[2]))

        if not matches:
            log_area.success(f"🎉 すべての該当ページ処理が完了しました（ページ {current_page} に商品なし）。")
            break

        log_area.text(f"ページ {current_page} から {len(matches)} 件を検知。個別解析中...")

        new_rows_buffer = []
        now_str = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

        for idx, match in enumerate(matches):
            used_path, full_title, _ = match
            
            pack = ""
            card_no = ""
            rarity = ""
            name = ""

            pack_match = re.search(r'\((.*?)\)$', full_title)
            if pack_match: pack = pack_match.group(1)
            
            bracket_match = re.search(r'\[(.*?)\]', full_title)
            if bracket_match: card_no = bracket_match.group(1)

            before_bracket = full_title.split("[")[0].strip()
            for r in RARITY_LIST:
                if before_bracket.endswith(f" {r}"):
                    rarity = r
                    name = before_bracket[:-(len(r)+1)].strip()
                    break
            if not name: name = before_bracket

            clean_path = used_path.split('?')[0]
            id_match = re.search(r'\/apparels\/(\d+)', clean_path)
            apparel_id = id_match.group(1) if id_match else "730964"
            clean_product_url = f"https://snkrdunk.com/apparels/{apparel_id}"

            search_query = f"{name} {card_no} PSA10".strip()
            direct_search_url = f"https://snkrdunk.com/search?keywords={requests.utils.quote(search_query)}"
            
            log_area.text(f"[{idx+1}/{len(matches)}] PSA10相場判定中: {name} ({card_no})")
            
            psa_prices = []
            try:
                s_res = requests.get(direct_search_url, headers=SPOOFED_HEADERS, timeout=10)
                time.sleep(2.5)
                search_html = s_res.text
                product_blocks = re.split(r'<a[^>]*href=', search_html, flags=re.IGNORECASE)
                
                for block in product_blocks[1:]:
                    if "PSA10" in block and (name in block or (card_no and card_no in block)):
                        p_match = re.search(r'-\s*¥\s*([\d,]+)', block, re.IGNORECASE) or re.search(r'¥\s*([\d,]+)', block, re.IGNORECASE)
                        if p_match:
                            num_price = int(p_match.group(1).replace(",", ""))
                            if num_price > 100:
                                psa_prices.append(num_price)
            except Exception as e:
                pass

            psa_median = ""
            if psa_prices:
                psa_prices.sort()
                lowest_prices = psa_prices[:6]
                half = len(lowest_prices) // 2
                if len(lowest_prices) % 2 != 0:
                    psa_median = lowest_prices[half]
                else:
                    psa_median = round((lowest_prices[half - 1] + lowest_prices[half]) / 2)

            key = f"{name}_{rarity}_{card_no}"
            if key in pokemon_map:
                row_info = pokemon_map[key]
                final_price = psa_median if psa_median != "" else row_info["price"]
                
                sheet.update_cell(row_info["row_num"], 5, final_price)
                sheet.update_cell(row_info["row_num"], 6, now_str)
                sheet.update_cell(row_info["row_num"], 7, clean_product_url)
            else:
                new_rows_buffer.append([name, rarity, card_no, pack, psa_median, now_str, clean_product_url])
                pokemon_map[key] = {"row_num": len(existing_rows) + len(new_rows_buffer) + 1, "price": psa_median}

            all_success_count += 1

        if new_rows_buffer:
            sheet.append_rows(new_rows_buffer)
            existing_rows = sheet.get_all_values()

        current_page += 1

    progress_bar.progress(1.0)
    st.balloons()
    st.success(f"全体の処理が完了しました！計 {all_success_count} 件のカード情報を同期しました。")
