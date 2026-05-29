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
    client = gspread.authorize(creds)
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
# 価格＆開いたページのURL取得ロジック（スクロール追跡強化版）
# =========================================

def get_psa10_data_from_page(page, product_url, card_name):
    """他状態の長さに負けないよう、スクロールしながらPSA10の要素を追跡する"""
    target_median = "なし"
    final_url = product_url.split("/sales-histories")[0].split("?")[0]
    history_url = f"{final_url}/sales-histories?slide=right"
    
    shot_before = None
    shot_after = None

    try:
        # 相場専用ページへ移動
        page.goto(history_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.0)

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

        # 1回目の撮影（アクセス＆ポップアップ処理直後）
        try:
            shot_before = page.screenshot(full_page=False)
        except:
            pass

        # 【超重要】「状態PSA10の売買履歴」の文字が見つかるまで、少しずつ下にスクロールしながら探す
        found_psa10 = False
        for _ in range(15):  # 最大15回、少しずつ下にスクロール
            # ページ内に「状態PSA10の売買履歴」という見出しがあるかチェック
            h2_exists = page.evaluate("""
                () => {
                    const headings = Array.from(document.querySelectorAll('h2.size-title'));
                    return headings.some(h => h.textContent.includes('状態PSA10の売買履歴'));
                }
            """)
            
            if h2_exists:
                # 見つかったら、その要素が画面に見える位置までスクロールさせる
                try:
                    page.locator("h2.size-title:has-text('状態PSA10の売買履歴')").scroll_into_view_if_needed()
                    found_psa10 = True
                except:
                    pass
                break
            
            # まだ見つからなければ、1画面分の半分（500px）ずつ下にスクロールして遅延読み込みを発生させる
            page.evaluate("window.scrollBy(0, 500)")
            time.sleep(0.6)  # 読み込みを待つための微小バッファ

        # 2回目の撮影（スクロール追跡完了後、PSA10が画面に捉えられているはずのタイミング）
        try:
            shot_after = page.screenshot(full_page=False)
        except:
            pass

        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        psa10_prices = []
        
        # 「状態PSA10の売買履歴」という見出しをピンポイント検索
        h2_tags = soup.find_all("h2", class_="size-title")
        psa10_ul = None
        
        for h2 in h2_tags:
            if "状態PSA10の売買履歴" in h2.get_text():
                psa10_ul = h2.find_next("ul", class_="sales-history")
                break
        
        if psa10_ul:
            history_items = psa10_ul.select("li")
            for item in history_items:
                size_elem = item.select_one("p.size")
                price_elem = item.select_one("p.price")

                if size_elem and price_elem:
                    size_text = size_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)

                    if "PSA10" in size_text:
                        clean_price = int(re.sub(r"[^\d]", "", price_text))
                        psa10_prices.append(clean_price)

        if psa10_prices:
            recent_6_prices = psa10_prices[:6]
            target_median = int(median(recent_6_prices))

    except Exception as e:
        pass

    # セッション状態にスクショ履歴を追加保存
    if "screenshot_history" not in st.session_state:
        st.session_state.screenshot_history = []
        
    st.session_state.screenshot_history.append({
        "name": card_name,
        "url": history_url,
        "price": target_median,
        "shot_before": shot_before,
        "shot_after": shot_after,
        "time": datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%H:%M:%S")
    })

    return target_median, final_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（スクロール追跡版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（スクロール追跡版）")
st.write("ページ下部にあるPSA10の売買履歴を自動で追跡・スクロールして確実に取得するよう改善しました。")

if "running" not in st.session_state:
    st.session_state.running = False
if "current_page" not in st.session_state:
    st.session_state.current_page = 1
if "screenshot_history" not in st.session_state:
    st.session_state.screenshot_history = []

col1, col2, col3 = st
