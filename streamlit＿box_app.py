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
# 価格＆開いたページのURL取得ロジック（パック・タイムアウト完全対策版）
# =========================================

def get_box_data_from_page(page, product_url):
    """パック・ボックス両対応、ロード待ちを極限まで強化して直近6件の中央値を計算"""
    target_median = "なし"
    final_url = product_url

    try:
        page.goto(product_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.5)  # 基本の待機時間を少し延長して安定化

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

        # 【強化1】売買履歴の親要素が表示され、かつ中身（テキスト）が読み込まれるまで最大20秒待つ
        try:
            page.wait_for_selector("ul.sales-history.item-list", state="attached", timeout=20000)
            # 履歴の最初の子要素に文字が入るまで数秒追加待機
            page.wait_for_function(
                "() => document.querySelector('ul.sales-history.item-list li p.price') !== null",
                timeout=5000
            )
        except Exception:
            pass

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        unit_prices = []
        history_items = soup.select("ul.sales-history.item-list li")

        for item in history_items:
            size_elem = item.select_one("p.size")    # 「1個」「30パック」など
            price_elem = item.select_one("p.price")  # 「¥30,000」「¥19200」など

            if size_elem and price_elem:
                size_text = size_elem.get_text(strip=True)
                price_text = price_elem.get_text(strip=True)

                try:
                    # 【強化2】「個」でも「パック」でも、文字列に含まれるすべての「数字だけ」を抽出
                    clean_size = "".join([c for c in size_text if c.isdigit()])
                    clean_price = "".join([c for c in price_text if c.isdigit()])
                    
                    if not clean_size or not clean_price:
                        continue
                        
                    count = int(clean_size)
                    total_price = int(clean_price)
                    
                    if count > 0:
                        # 1個（または1パック）あたりの価格を計算
                        unit_price = int(total_price / count)
                        unit_prices.append(unit_price)
                except Exception:
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
st.write("ボックス・パックの一覧から、1個（パック）あたりの直近6件中央値価格を算出してシートへ同期します。")

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
    
    current_total_rows = len(existing_rows)
    box_map = {}

    if existing_rows:
        for idx, row in enumerate(existing_rows[1:], start=2):
            while len(row) < 4:
                row.append("")
            key = row[0].strip()
            box_map[key] = {"row_num": idx}

    log_area = st.empty()
    progress_bar = st.progress(0)
    
    processed_in_this_run = set()

    while st.session_state.running:
        current_page = st.session_state.current_page
        log_area.markdown(f"## 📄 現在、一覧の **ページ {current_page}** を解析中...")
        
        url = (
            f"https://snkrdunk.com/search?"
            f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
            f"&searchCategoryIds=6,6%2F26"
            f"&brandIds=pokemon"
            f"&sort=hottest"
            f"&itemConditions=brand_new"
            f"&itemSizes=quantity_1"
            f"&page={current_page}"
        )

        try:
            res = requests.get(url, headers=SPOOFED_HEADERS, timeout=20)
        except Exception as e:
            st.error(f"❌ 一覧取得で通信エラーが発生しました(Page {current_page}): {e}")
            st.session_state.running = False
            break

        if res.status_code == 404:
            st.success(f"🎉 最終ページに到達したため、全巡回を完了しました！（最終: {current_page-1}ページ）")
            st.session_state.current_page = 1
            st.session_state.running = False
            break
        elif res.status_code != 200:
            st.error(f"❌ ページの取得に失敗しました。Status: {res.status_code}")
            st.session_state.running = False
            break

        matches = re.findall(r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ', res.text)

        if not matches:
            st.success(f"🎉 商品が見つからなくなったため、全ページの巡回を完了しました！（合計: {current_page-1}ページ走破）")
            st.session_state.current_page = 1
            st.session_state.running = False
            break

        matches = matches[:30]
        new_rows = []
        total_items = len(matches)

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

            for idx, match in enumerate(matches):
                if not st.session_state.running:
                    break

                href = match[0]
                
                raw_name = match[1].strip()
                name = raw_name.replace("ポケモンカードゲーム", "").replace("&amp;", "&").strip()

                id_match = re.search(r'/(?:products|apparels)/(?:used/)?(\d+)', href)
                if not id_match:
                    continue
                
                card_id = id_match.group(1)
                access_url = f"https://snkrdunk.com/apparels/{card_id}"
                
                if name in processed_in_this_run:
                    continue
                processed_in_this_run.add(name)

                progress_bar.progress((idx + 1) / total_items)
                log_area.markdown(f"### 🔄 処理中({idx+1}/{total_items}件目): **{name}** (ページ {current_page})")

                unit_median_price, real_product_url = get_box_data_from_page(page, access_url)
                
                now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
                row_data = [name, unit_median_price, now_str, real_product_url]

                if name in box_map:
                    row_num = box_map[name]["row_num"]
                    sheet.update(f"A{row_num}:D{row_num}", [row_data])
                    st.toast(f"✏️ 【上書き更新】{name} -> 単価 ¥{unit_median_price}")
                else:
                    new_rows.append(row_data)
                    st.toast(f"➕ 【新規追加】{name} -> 単価 ¥{unit_median_price}")
                    
                    current_total_rows += 1
                    box_map[name] = {"row_num": current_total_rows}

                time.sleep(random.uniform(3.0, 5.0)) # 負荷を考慮し、スクレイピング間隔をわずかに調整

            browser.close()

        if new_rows and st.session_state.running:
            sheet.append_rows(new_rows)

        if st.session_state.running:
            st.session_state.current_page += 1
            time.sleep(4.0)
        else:
            st.warning(f"🛑 ユーザーにより停止されました。（前回処理完了: {current_page} ページ目）")
            break

    st.session_state.running = False
    st.rerun()
