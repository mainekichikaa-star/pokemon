import streamlit as st
import requests
import re
import time
import random
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================
# Playwrightのブラウザ自動インストール処理
# =========================================
@st.cache_resource
def install_playwright_browsers():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        return True
    except Exception as e:
        st.error(f"Playwrightブラウザのインストールに失敗しました: {e}")
        return False

with st.spinner("システムを準備中..."):
    install_playwright_browsers()

from playwright.sync_api import sync_playwright

# =========================================
# 設定
# =========================================

SPREADSHEET_ID = "1HwNBcYJUSofFS-HkQI9eVLZWnuOJaXPzMmE8nC6E_bY"
SHEET_NAME = "カード"

HEADERS = [
    "名前",
    "レアリティ",
    "型番（カード番号）",
    "収録パック",
    "現在の価格(PSA10直近6件中央値)",
    "最終更新",
    "URL"
]

RARITY_LIST = [
    "SAR", "SR", "AR", "CHR", "CSR",
    "UR", "HR", "RRR", "RR", "R",
    "C", "U", "P", "PROMO", "MUR", "MA"
]

SPOOFED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
}

# =========================================
# Google Sheets
# =========================================

def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    service_account_info = dict(st.secrets["gcp_service_account"])
    if "private_key" in service_account_info:
        pk = service_account_info["private_key"]
        if "\\n" in pk:
            pk = pk.replace("\\n", "\n")
        service_account_info["private_key"] = pk

    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    client = gspread.authorize(creds)
    workbook = client.open_by_key(SPREADSHEET_ID)
    try:
        return workbook.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        sheet = workbook.add_worksheet(title=SHEET_NAME, rows="1000", cols="20")
        sheet.append_row(HEADERS)
        return sheet

def parse_title(full_title):
    pack = ""
    card_no = ""
    rarity = ""
    name = ""
    pack_match = re.search(r'\(([^)]+)\)$', full_title.strip())
    if pack_match:
        pack = pack_match.group(1)
    bracket_match = re.search(r'\[([^\]]+)\]', full_title)
    if bracket_match:
        card_no = bracket_match.group(1)
    before_bracket = full_title.split("[")[0].strip()
    rarity_pattern = r'(.*?)\s+(' + '|'.join(RARITY_LIST) + r')$'
    rarity_match = re.search(rarity_pattern, before_bracket)
    if rarity_match:
        name = rarity_match.group(1).strip()
        rarity = rarity_match.group(2).strip()
    else:
        name = before_bracket
    return name, rarity, card_no, pack

# =========================================
# 価格取得（最近の売買履歴からPSA10を抽出）
# =========================================

def get_psa10_price_debug(product_url, debug_container):
    base_url = product_url.split("/sales-histories")[0].split("?")[0]
    debug_container.info(f"🔍 解析対象URL: {base_url}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent=SPOOFED_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
                locale="ja-JP"
            )
            page = context.new_page()
            
            page.goto(base_url, wait_until="networkidle", timeout=60000)
            time.sleep(2.0)

            # Buyeeポップアップの消去処理
            page.evaluate("""
                () => {
                    const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                    if (closeBtn) { closeBtn.click(); return; }
                    
                    const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                    if (modal) { modal.remove(); }
                }
            """)
            time.sleep(1.0)

            # 「PSA10」のボタンを強制クリック（状態を絞り込むため一応実行）
            psa10_label = page.locator("label:has-text('PSA10')")
            if psa10_label.count() > 0:
                psa10_label.first.click(force=True)
                debug_container.success("🎯 PSA10の絞り込みボタンをクリックしました")
                time.sleep(2.0)

            # 最新のHTMLソースを取得してブラウザを閉じる
            html = page.content()
            browser.close()

        # -----------------------------------------
        # 【新ロジック】売買履歴リストから直接抽出
        # -----------------------------------------
        soup = BeautifulSoup(html, "html.parser")
        psa10_prices = []

        # 履歴の各行(li要素)をループ処理
        history_items = soup.select("ul.sales-history.item-list li")
        debug_container.write(f"📋 見つかった全体の売買履歴件数: {len(history_items)}件")

        for item in history_items:
            size_elem = item.select_one("p.size")
            price_elem = item.select_one("p.price")

            if size_elem and price_elem:
                size_text = size_elem.get_text(strip=True)
                price_text = price_elem.get_text(strip=True)

                # 状態が「PSA10」のものだけを厳選して格納
                if "PSA10" in size_text:
                    # 「¥31,500」から数字だけを抽出して数値化
                    clean_price = int(re.sub(r"[^\d]", "", price_text))
                    psa10_prices.append(clean_price)

        debug_container.write(f"💎 履歴から抽出したPSA10限定の価格リスト: {psa10_prices}")

        # 直近6件（上から順に最新データなので、最初の6件）を対象にする
        if psa10_prices:
            recent_6_prices = psa10_prices[:6]
            debug_container.info(f"⏱️ 直近のPSA10（最大6件）: {recent_6_prices}")
            
            # 中央値を計算して戻す
            target_median = int(median(recent_6_prices))
            debug_container.success(f"📈 算出された中央値: ¥{target_median:,}")
            return target_median
        else:
            debug_container.warning("⚠️ 売買履歴からPSA10のデータが見つかりませんでした。")
            return "なし"

    except Exception as e:
        debug_container.error(f"🚨 エラー詳細: {str(e)}")
        return "なし"

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（履歴直接解析版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（履歴ダイレクト解析）")
st.write("売買履歴HTMLから直接PSA10を検知し、直近6件の中央値を割り出します。")

start_button = st.button("🔄 検証モードで更新開始", type="primary")

if start_button:
    sheet = get_sheet()
    existing_rows = sheet.get_all_values()
    pokemon_map = {}

    if existing_rows:
        for idx, row in enumerate(existing_rows[1:], start=2):
            while len(row) < 7:
                row.append("")
            key = f"{row[0]}_{row[1]}_{row[2]}"
            pokemon_map[key] = {"row_num": idx}

    log_area = st.empty()
    debug_container = st.container()

    current_page = 1
    log_area.markdown(f"## 📝 現在の処理: ページ {current_page} のみ")

    url = (
        "https://snkrdunk.com/search?"
        "keywords=%E3%83%88%E3%83%AC%E3%82%AB+"
        "%28%E3%82%B7%E3%83%B3%E3%82%B0%E3%83%AB"
        "%E3%82%AB%E3%83%BC%E3%83%89%29"
        "&searchCategoryIds=6%2F33"
        "&brandIds=pokemon"
        "&sort=hottest"
        f"&page={current_page}"
    )

    try:
        res = requests.get(url, headers=SPOOFED_HEADERS, timeout=20)
    except Exception as e:
        st.error(f"一覧取得エラー: {e}")
        st.stop()

    matches = re.findall(r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ', res.text)

    if not matches:
        st.warning("商品リンクが見つかりませんでした。")
        st.stop()

    now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
    new_rows = []

    for idx, match in enumerate(matches):
        href = match[0]
        full_title = match[1]

        clean_path = href.split("?")[0].replace("/products/", "/apparels/").replace("/used", "")
        product_url = clean_path if clean_path.startswith("http") else "https://snkrdunk.com" + clean_path

        name, rarity, card_no, pack = parse_title(full_title)
        
        log_area.markdown(f"### 🔄 現在処理中: **{name}** ({idx+1}/{len(matches)}件目)")
        
        with debug_container:
            st.markdown(f"---")
            psa_price = get_psa10_price_debug(product_url, st)

        key = f"{name}_{rarity}_{card_no}"
        row_data = [name, rarity, card_no, pack, psa_price, now_str, product_url]

        if key in pokemon_map:
            row_num = pokemon_map[key]["row_num"]
            sheet.update(f"A{row_num}:G{row_num}", [row_data])
        else:
            new_rows.append(row_data)

        if idx >= 2:
            st.info("検証用に最初の3件で一旦止めています。")
            break
            
        time.sleep(3.0)

    if new_rows:
        sheet.append_rows(new_rows)

    st.success("テスト処理完了！正確なPSA10データが中央値として計算されているか、ログをご確認ください。")
