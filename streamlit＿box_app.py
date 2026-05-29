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
SHEET_NAME = "パック・ボックス"

HEADERS = [
    "名前",
    "現在の価格",
    "最終更新",
    "URL"
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
        sheet = workbook.add_worksheet(title=SHEET_NAME, rows="1000", cols="10")
        sheet.append_row(HEADERS)
        return sheet

# =========================================
# 価格＆開いたページのURL取得ロジック（履歴読み込み待ち強化版）
# =========================================

def get_box_data_from_page(page, product_url):
    """1個あたりの価格を算出して直近6件の中央値を返す（履歴ロード待ちを追加）"""
    target_median = "なし"
    final_url = product_url

    try:
        page.goto(product_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.0)

        final_url = page.url.split("/sales-histories")[0].split("?")[0]

        # Buyeeポップアップの消去処理
        page.evaluate("""
            () => {
                const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                if (closeBtn) { closeBtn.click(); return; }
                const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                if (modal) { modal.remove(); }
            }
        """)
        time.sleep(0.3)

        # 【対策】非同期で読み込まれる売買履歴のリスト(li)が登場するまで最大15秒待つ
        try:
            page.wait_for_selector("ul.sales-history.item-list li", timeout=15000)
        except Exception:
            # 万が一タイムアウトした場合は、その時点のHTMLで処理を進める
            pass

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        unit_prices = []
        history_items = soup.select("ul.sales-history.item-list li")

        for item in history_items:
            size_elem = item.select_one("p.size")    # 例: "2個" "10個"
            price_elem = item.select_one("p.price")  # 例: "¥54,200"

            if size_elem and price_elem:
                size_text = size_elem.get_text(strip=True)
                price_text = price_elem.get_text(strip=True)

                # 数値だけを抽出
                try:
                    count = int(re.sub(r"[^\d]", "", size_text))
                    total_price = int(re.sub(r"[^\d]", "", price_text))
                    
                    if count > 0:
                        # 1個あたりの価格を計算（端数切り捨てでint型へ変換）
                        unit_price = int(total_price / count)
                        unit_prices.append(unit_price)
                except ValueError:
                    continue

        if unit_prices:
            # 直近最大6件分を抽出して中央値を計算
            recent_6_prices = unit_prices[:6]
            target_median = int(median(recent_6_prices))

    except Exception as e:
        pass

    return target_median, final_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ（パック・ボックス）価格自動反映", layout="wide")
st.title("📦 ポケカ価格自動反映ツール（パック・ボックス版）")
st.write("ボックス・パックの一覧から、1個あたりの直近6件中央値価格を算出してシートへ同期します。")

if "running" not in st.session_state:
    st.session_state.running = False
if "current_page" not in st.session_state:
    st.session_state.current_page = 1

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔄 最初から（1ページ目）更新を開始", type="primary", disabled=st.session_state.running):
        st.session_state.current_page = 1
        st.session_state.running = True
        st.rerun()

with col2:
    resume_label = f"▶️ 続きから再開（現在: {st.session_state.current_page} ページ目）"
    if st.button(resume_label, type="secondary", disabled=st.session_state.running or st.session_state.current_page == 1):
        st.session_state.running = True
        st.rerun()

with col3:
    stop_button = st.button("🛑 処理を停止する", type="secondary", disabled=not st.session_state.running)

if stop_button:
    st.session_state.running = False
    st.warning("🛑 停止要請を受け付けました。現在のページの同期完了後に安全に停止します...")
    st.rerun()

# =========================================
# メイン巡回ループ
# =========================================

if st.session_state.running:
    sheet = get_sheet()
    
    st.info("📊 最新データを取得するため、Googleシートをスキャン中...")
    existing_rows = sheet.get_all_values()
    
    # 現在のシート上のデータ件数を正確に把握
    current_total_rows = len(existing_rows)
    box_map = {}

    if existing_rows:
        for idx, row in enumerate(existing_rows[1:], start=2):
            while len(row) < 4:
                row.append("")
            key = row[0].strip() # 「整形後の名前」をキーにして重複・更新チェック
            box_map[key] = {"row_num": idx}

    log_area = st.empty()
    progress_bar = st.progress(0)
    
    processed_in_this_run = set()

    while st.session_state.running:
        current_page = st.session_state.current_page
        log_area.markdown(f"## 📄 現在、一覧の **ページ {current_page}** を解析中...")
        
        # 指定されたボックス・パック用のURL
        url = (
            f"https://snkrdunk.com/search?"
            f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
            f"&searchCategoryIds=6,6%2F26"
            f"&brandIds=pokemon"
            f"&sort=hottest"
            f"&itemConditions=brand_new"
            f"&itemSizes=quantity_1"
            f"&page={current_page}"
