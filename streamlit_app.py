import streamlit as st
import requests
import re
import time
import random
import subprocess
import html  # 文字化け（&amp;等）の解除用
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
SHEET_NAME = "カード（PSA10）"

HEADERS = [
    "名前",
    "レアリティ",
    "品番",
    "収録パック",
    "現在の価格",
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
    client = gspread.authorizecreds(creds)
    workbook = client.open_by_key(SPREADSHEET_ID)
    try:
        return workbook.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        sheet = workbook.add_worksheet(title=SHEET_NAME, rows="1000", cols="20")
        sheet.append_row(HEADERS)
        return sheet

def parse_title(full_title):
    full_title = html.unescape(full_title)
    
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
# 価格＆開いたページのURL取得ロジック（検証用スクショ付き）
# =========================================

def get_psa10_data_from_page(page, product_url, screenshot_area):
    """他状態を無視してPSA10を狙い撃ち＋待機処理強化＋ページ確認用スクショ機能"""
    target_median = "なし"
    final_url = product_url.split("/sales-histories")[0].split("?")[0]
    history_url = f"{final_url}/sales-histories?slide=right"

    try:
        # 相場専用ページへ移動
        page.goto(history_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.5)

        # Buyeeポップアップの消去
        page.evaluate("""
            () => {
                const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                if (closeBtn) { closeBtn.click(); return; }
                const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                if (modal) { modal.remove(); }
            }
        """)
        time.sleep(0.5)

        # 【追加】「状態PSA10の売買履歴」の文字がHTML内に出現するまで最大15秒間じっと待つ（読み込み遅延対策）
        try:
            page.wait_for_selector("h2.size-title:has-text('状態PSA10の売買履歴')", timeout=15000)
        except:
            pass # タイムアウトしても次へ進む

        # 【追加】検証用のスクリーンショット撮影（メモリ上に保存してStreamlitに表示）
        screenshot_bytes = page.screenshot(full_page=False)
        screenshot_area.image(screenshot_bytes, caption=f"📸 現在のブラウザ目
