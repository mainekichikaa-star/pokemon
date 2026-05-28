import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import time
import json
from datetime import datetime
import numpy as np
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# スプレッドシートID
SPREADSHEET_ID = "1HwNBcYJUSofFS-HkQI9eVLZWnuOJaXPzMmE8nC6E_bY"
SHEET_NAME = "カード"

# 1. Googleスプレッドシートへの接続設定
@st.cache_resource
def get_gspread_client():
    # StreamlitのSecretsから認証情報を取得
    if "gcp_service_account" in st.secrets:
        creds_dict = json.loads(st.secrets["gcp_service_account"])
    else:
        st.error("StreamlitのSecretsに 'gcp_service_account' が設定されていません。")
        st.stop()
        
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def main():
    st.title("🃏 ポケカ PSA10価格自動更新アプリ")
    st.write("スニダンから最新の価格情報を取得し、Googleスプレッドシートを全ページ一括更新します。")

    if st.button("🔄 全ページ価格更新をスタートする", type="primary"):
        status_text = st.empty()
        log_area = st.empty()
        
        try:
            client = get_gspread_client()
            ss = client.open_by_key(SPREADSHEET_ID)
            
            # シートの存在チェック。なければ作成
            try:
                sheet = ss.worksheet(SHEET_NAME)
            except gspread.exceptions.WorksheetNotFound:
                sheet = ss.add_worksheet(title=SHEET_NAME, rows="1000", cols="10")
                
            headers = ["名前", "レアリティ", "型番（カード番号）", "収録パック", "現在の価格(PSA10中央値)", "最終更新", "URL"]
            
            # 既存データの取得
            existing_values = sheet.get_all_values()
            pokemon_map = {}
            
            if len(existing_values) == 0 or existing_values[0][0] != "名前":
                sheet.insert_row(headers, 1)
                existing_values = [headers]
            else:
                # 既存データをマップ化して更新行を特定できるようにする (行番号は1個ずれるので i+1)
                for i, row in enumerate(existing_values):
                    if i == 0 or len(row) < 3:
                        continue
                    # キー: 名前_レアリティ_型番
                    key = f"{row[0]}_{row[1]}_{row[2]}"
                    pokemon_map[key] = {"row_num": i + 1, "price": row[4] if len(row) > 4 else ""}

            spoofed_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
            }

            current_page = 1
            new_rows_buffer = []
            now_str = datetime.now().strftime("%Y/%m/dd %H:%m:%S")
            logs = []

            # バックグラウンド制限なしで全ページループ
            while True:
                status_text.markdown(### 🚀 現在のステータス: ページ {current_page} を取得中...`)
                
                url = (
                    f"https://snkrdunk.com/search?"
                    f"keywords=%E3%83%88%E3%83%AC%E3%82%AB+%28%E3%82%B7%E3%83%B3%E3%82%B0%E3%83%AB%E3%82%AB%E3%83%BC%E3%83%89%29"
                    f"&searchCategoryIds=6%2F33"
                    f"&brandIds=pokemon"
                    f"&sort=hottest"
                    f"&page={current_page}"
                )

                try:
                    res = requests.get(url, headers=spoofed_headers, timeout=15)
                    time.sleep(2.0)  # 安全ウェイト
                except Exception as e:
                    logs.append(f"❌ ページ {current_page} で通信エラー: {str(e)}")
                    log_area.code("\n".join(logs[-15:]))
                    time.sleep(5.0)
                    continue

                html = res.text
                
                # 商品ブロックの抽出 (Regex)
                product_regex = r'<a[^>]*href="([^"]+?/apparels/[^"]+?/used)"[^>]*aria-label="([^"]+?) - ¥([\d,]+)"'
                matches = re.findall(product_regex, html)

                if not matches:
                    # フォールバック
                    fallback_regex = r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ¥([\d,]+)"'
                    fb_matches = re.findall(fallback_regex, html)
                    for f_match in fb_matches:
                        path = f_match[0].split('?')[0]
                        if "/products/" in path:
                            path = path.replace("/products/", "/apparels/")
                        if "/used" not in path:
                            path = path + "/used"
                        matches.append((path, f_match[1], f_match[2]))

                # 商品が1件もなくなったら最終ページと判断してループを抜ける
                if not matches:
                    logs.append(f"🏁 ページ {current_page} に商品が見つかりません。全ページ走査を完了しました。")
                    log_area.code("\n".join(logs[-15:]))
                    break

                logs.append(f"📦 ページ {current_page} から {len(matches)} 件の商品情報を検知しました。")
                log_area.code("\n".join(logs[-15:]))

                for item in matches:
                    used_path, full_title, _ = item
                    
                    name, rarity, card_no, pack = "", "", "", ""
                    
                    pack_match = re.search(r'\((.*?)\)$', full_title)
                    if pack_match:
                        pack = pack_match.group(1)
                    
                    bracket_match = re.search(r'\[(.*?)\]', full_title)
                    if bracket_match:
                        card_no = bracket_match.group(1)

                    before_bracket = full_title.split("[")[0].strip()
                    rarity_list = ["SAR","SR","AR","CHR","CSR","UR","HR","RRR","RR","R","C","U","P","PROMO","MUR","MA"]
                    
                    for r in rarity_list:
                        if before_bracket.endswith(" " + r):
                            rarity = r
                            name = re.sub(r'\s' + r + '$', '', before_bracket).strip()
                            break
                    if not name:
                        name = before_bracket

                    clean_path = used_path.split('?')[0]
                    id_match = re.search(r'/apparels/(\d+)', clean_path)
                    apparel_id = id_match.group(1) if id_match else "730964"
                    clean_product_url = f"https://snkrdunk.com/apparels/{apparel_id}"

                    # 各カードのPSA10検索へ
                    search_query = f"{name} {card_no} PSA10".strip()
                    direct_search_url = f"https://snkrdunk.com/search?keywords={requests.utils.quote(search_query)}"

                    logs.append(f"🔍 価格調査中: {name} ({card_no})")
                    log_area.code("\n".join(logs[-15:]))

                    try:
                        search_res = requests.get(direct_search_url, headers=spoofed_headers, timeout=15)
                        time.sleep(2.5)  # 負荷軽減ウェイト
                        search_html = search_res.text
                    except Exception as e:
                        logs.append(f"   ⚠️ 照会エラー: {str(e)}")
                        log_area.code("\n".join(logs[-15:]))
                        continue

                    # HTMLをaタグの枠ごとに分解
                    product_blocks = re.split(r'<a[^>]*href=', search_html, flags=re.IGNORECASE)
                    psa_prices = []

                    for block in product_blocks[1:]:
                        # PSA10かつ名前かカード番号が含まれるマスに限定して隔離解析
                        if re.search(r'PSA10', block, re.IGNORECASE) and (name in block or (card_no and card_no in block)):
                            price_match = re.search(r'-\s*¥\s*([\d,]+)', block, re.IGNORECASE) or re.search(r'¥\s*([\d,]+)', block, re.IGNORECASE)
                            if price_match:
                                num_price = int(price_match.group(1).replace(",", ""))
                                if num_price > 100:
                                    psa_prices.append(num_price)

                    # 【直近6件厳選の中央値ロジック】
                    psa_median = ""
                    if psa_prices:
                        psa_prices.sort()
                        lowest_prices = psa_prices[:6]  # 直近（最安順ソート後）の最大6件
                        psa_median = int(np.median(lowest_prices))
                        logs.append(f"   ➔ 成功! 全体:{len(psa_prices)}件 ➔ 最安6件: {lowest_prices} ➔ 中央値: ¥{psa_median}")
                    else:
                        logs.append(f"   ➔ ⚠️ PSA10出品データなし")
                    log_area.code("\n".join(logs[-15:]))

                    key = f"{name}_{rarity}_{card_no}"
                    
                    # スプレッドシートへの書き込み判定
                    if key in pokemon_map:
                        # 既存データ上書き
                        existing = pokemon_map[key]
                        final_price = psa_median if psa_median != "" else existing["price"]
                        
                        # セル単位更新 (速度向上のため変更があった場合のみ行うなどカスタマイズも可能)
                        sheet.update_cell(existing["row_num"], 5, final_price)
                        sheet.update_cell(existing["row_num"], 6, now_str)
                        sheet.update_cell(existing["row_num"], 7, clean_product_url)
                    else:
                        # 新規行バッファ
                        new_rows_buffer.append([name, rarity, card_no, pack, psa_median, now_str, clean_product_url])
                        # ループ内重複回避
                        pokemon_map[key] = {"row_num": len(existing_values) + len(new_rows_buffer) + 1, "price": psa_median}

                # ページごとに新規行をまとめて追加
                if new_rows_buffer:
                    sheet.append_rows(new_rows_buffer)
                    new_rows_buffer.clear()

                current_page += 1

            status_text.success("🎉 すべてのカードの更新が完了しました！")

        except Exception as global_e:
            st.error(f"プログラム全体でエラーが発生しました: {str(e)}")

if __name__ == "__main__":
    main()
