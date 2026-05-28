import streamlit as st
import requests
import re
import time
import random
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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
# 停止フラグ
# =========================================

if "stop_flag" not in st.session_state:
    st.session_state.stop_flag = False

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

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        service_account_info,
        scope
    )

    client = gspread.authorize(creds)
    workbook = client.open_by_key(SPREADSHEET_ID)

    try:
        return workbook.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        sheet = workbook.add_worksheet(
            title=SHEET_NAME,
            rows="1000",
            cols="20"
        )
        sheet.append_row(HEADERS)
        return sheet

# =========================================
# タイトル解析
# =========================================

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
# PSA10価格取得（グラフ解析版）
# =========================================

def get_psa10_price(product_url):
    # 個別ページのURLの形を担保
    base_url = product_url.split("/sales-histories")[0].split("?")[0]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=SPOOFED_HEADERS["User-Agent"])
            page = context.new_page()
            
            # 個別商品ページへ直接移動
            page.goto(base_url, wait_until="load", timeout=60000)
            
            # 「PSA10」のラベル要素を探してクリック
            psa10_label = page.locator("label:has-text('PSA10')")
            if psa10_label.count() > 0:
                psa10_label.first.click()
                time.sleep(1.5)  # グラフのデータが切り替わるのを少し待つ
            else:
                browser.close()
                return "なし"

            # Highchartsの内部オブジェクトから、現在表示されているグラフデータをJavaScriptで直接引っこ抜く
            chart_data_json = page.evaluate("""
                () => {
                    const charts = window.Highcharts ? window.Highcharts.charts : [];
                    const activeChart = charts.find(c => c && c.series && c.series.length > 0);
                    if (activeChart) {
                        // グラフにプロットされている点（売買履歴データ）を取り出す
                        return activeChart.series[0].options.data.map(p => {
                            // p が配列 [タイムスタンプ, 価格] か オブジェクト {x: , y: } かで分岐
                            if (Array.isArray(p)) return p[1];
                            if (p && typeof p === 'object') return p.y;
                            return p;
                        });
                    }
                    return null;
                }
            """)
            
            browser.close()

        if not chart_data_json:
            return "なし"

        # 有効な数値のみをフィルター（Noneなどを除去）し、直近データ（配列の末尾側）から最大6件取得
        valid_prices = [int(p) for p in chart_data_json if p is not None and str(p).isdigit() or isinstance(p, (int, float))]
        
        if not valid_prices:
            return "なし"

        # グラフデータは古い順 -> 新しい順に並んでいるため、末尾（直近）から6件を切り出す
        recent_prices = valid_prices[-6:]

        # 直近6件の中央値を計算して返す
        return int(median(recent_prices))

    except Exception as e:
        print(f"ERROR ({base_url}):", e)
        return "なし"

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(
    page_title="ポケカ価格自動反映",
    layout="wide"
)

st.title("🃏 ポケカ価格自動反映ツール")
st.write("個別ページのPSA10グラフから直近6件の中央値を取得します")

col1, col2 = st.columns(2)

with col1:
    start_button = st.button("🔄 更新開始", type="primary")

with col2:
    stop_button = st.button("⛔ 停止")

if stop_button:
    st.session_state.stop_flag = True
    st.warning("停止リクエスト受付")

# =========================================
# 実行
# =========================================

if start_button:
    st.session_state.stop_flag = False

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
    progress_bar = st.progress(0)

    current_page = 1
    max_pages = 2
    total_count = 0

    session = requests.Session()

    while current_page <= max_pages:
        if st.session_state.stop_flag:
            st.warning("処理停止")
            st.stop()

        progress_bar.progress(current_page / max_pages)
        log_area.markdown(f"## ページ {current_page}")

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
            res = session.get(url, headers=SPOOFED_HEADERS, timeout=20)
        except Exception as e:
            st.error(e)
            break

        if res.status_code != 200:
            st.error(f"一覧取得失敗 {res.status_code}")
            break

        html = res.text

        product_regex = r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - '
        matches = re.findall(product_regex, html)

        if not matches:
            st.warning("商品なし")
            break

        now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
        new_rows = []

        for idx, match in enumerate(matches):
            if st.session_state.stop_flag:
                st.warning("処理停止")
                st.stop()

            href = match[0]
            full_title = match[1]

            clean_path = href.split("?")[0]

            if "/products/" in clean_path:
                clean_path = clean_path.replace("/products/", "/apparels/")
            
            clean_path = clean_path.replace("/used", "")

            if not clean_path.startswith("http"):
                product_url = "https://snkrdunk.com" + clean_path
            else:
                product_url = clean_path

            name, rarity, card_no, pack = parse_title(full_title)
            log_area.text(f"[{idx+1}/{len(matches)}] {name} (価格取得中...)")

            # =====================================
            # PSA10価格取得（個別ページのグラフから抽出）
            # =====================================
            psa_price = get_psa10_price(product_url)

            key = f"{name}_{rarity}_{card_no}"
            row_data = [
                name,
                rarity,
                card_no,
                pack,
                psa_price,
                now_str,
                product_url
            ]

            # =====================================
            # スプレッドシート更新
            # =====================================
            if key in pokemon_map:
                row_num = pokemon_map[key]["row_num"]
                sheet.update(f"A{row_num}:G{row_num}", [row_data])
            else:
                new_rows.append(row_data)

            total_count += 1
            # スニダンへの負荷軽減とBAN回避のためランダムにウェイトを入れる
            time.sleep(random.uniform(1.5, 3.0))

        if new_rows:
            sheet.append_rows(new_rows)

        current_page += 1

    progress_bar.progress(1.0)
    st.success(f"完了 {total_count} 件")
    st.balloons()
