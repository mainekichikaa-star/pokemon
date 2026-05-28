import streamlit as st
import requests
import re
import time
import random
import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================
# 【重要】Playwrightのブラウザ自動インストール処理
# =========================================
@st.cache_resource
def install_playwright_browsers():
    try:
        # 画面にインストール中であることを出さないよう、バックグラウンドで実行
        subprocess.run(["playwright", "install", "chromium"], check=True)
        return True
    except Exception as e:
        st.error(f"Playwrightブラウザのインストールに失敗しました: {e}")
        return False

# ブラウザのセットアップを実行
with st.spinner("システムを準備中..."):
    install_playwright_browsers()

# インストール完了後に読み込む
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

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        service_account_info,
        scope
    )
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
# 【画面出力デバッグ版】PSA10価格取得
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
            
            # ページへ移動
            page.goto(base_url, wait_until="networkidle", timeout=60000)
            time.sleep(2.0)

            # アクセス直後の画面キャプチャを表示
            img_bytes = page.screenshot()
            debug_container.image(img_bytes, caption="【デバッグ】ページアクセス時の画面")

            # 「PSA10」のボタンを探す
            psa10_label = page.locator("label:has-text('PSA10')")
            label_count = psa10_label.count()
            debug_container.write(f"📊 画面内の「PSA10」ボタン発見数: {label_count}")

            if label_count > 0:
                psa10_label.first.click()
                debug_container.success("クリック成功、データ読み込みを待機中...")
                time.sleep(3.0)
                
                # クリック後の画面
                img_bytes_after = page.screenshot()
                debug_container.image(img_bytes_after, caption="【デバッグ】PSA10クリック後の画面")
            else:
                debug_container.error("❌ PSA10という文字の入ったボタンが見つかりません。")
                browser.close()
                return "なし"

            # グラフ内部のJavaScriptデータを抽出
            chart_data = page.evaluate("""
                () => {
                    const charts = window.Highcharts ? window.Highcharts.charts : [];
                    const activeChart = charts.find(c => c && c.series && c.series.length > 0);
                    if (activeChart) {
                        return activeChart.series[0].options.data.map(p => {
                            if (Array.isArray(p)) return p[1];
                            if (p && typeof p === 'object') return p.y;
                            return p;
                        });
                    }
                    return null;
                }
            """)
            
            html = page.content()
            browser.close()

        debug_container.write(f"📈 グラフから抽出された生の配列データ: {chart_data}")

        if chart_data:
            valid_prices = [int(p) for p in chart_data if p is not None and (str(p).isdigit() or isinstance(p, (int, float)))]
            if valid_prices:
                recent_prices = valid_prices[-6:]
                debug_container.write(f"💰 直近6件の価格: {recent_prices}")
                return int(median(recent_prices))

        # バックアップ用：HTMLテキスト全検索
        debug_container.warning("⚠️ グラフデータが空のため、HTMLのテキスト全スキャンを試みます。")
        soup = BeautifulSoup(html, "html.parser")
        raw_prices = []
        for text in soup.find_all(text=True):
            clean_text = text.strip()
            if "¥" in clean_text or "," in clean_text:
                num_str = re.sub(r"[^\d]", "", clean_text)
                if num_str.isdigit() and len(num_str) >= 3:
                    val = int(num_str)
                    if 500 < val < 10000000:
                        raw_prices.append(val)

        debug_container.write(f"🔍 テキストから見つかった金額っぽい数字: {raw_prices[:15]}")
        if raw_prices:
            return int(median(raw_prices[-6:]))

        return "なし"

    except Exception as e:
        debug_container.error(f"🚨 実行エラー発生: {str(e)}")
        return "なし"

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（デバッグ版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（1ページ限定検証モード）")
st.write("環境依存エラーを対策。1ページ目のみ処理を実行し、画面上にリアルタイムログを表示します。")

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
        st.error(f"一覧取得でのエラー: {e}")
        st.stop()

    if res.status_code != 200:
        st.error(f"一覧取得失敗 ステータスコード: {res.status_code}")
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

    st.success("1ページ目のテスト処理が完了しました。")
