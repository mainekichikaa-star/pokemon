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
# 価格取得（広告削除＆PSA10抽出ロジック）
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

            # -----------------------------------------
            # 【最重要】Buyeeポップアップの「×」ボタンを検知して消し去る処理
            # -----------------------------------------
            # 共有画像で見えている、右上の丸い「×」ボタン（buyee-bcF-modal-close）のCSSセレクター
            buyee_close_button = page.locator(".buyee-bcF-modal-close, #buyee-bcFrameClose, .buyee-close")
            
            # iframe（別枠埋め込み）の中に×ボタンがある場合も考慮して、ページ全体からクラス名を探すJavaScript
            page.evaluate("""
                () => {
                    // 通常のDOMから×ボタンを強制クリックして消す
                    const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                    if (closeBtn) { closeBtn.click(); return; }
                    
                    # ポップアップの親要素ごと非表示(削除)にする
                    const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                    if (modal) { modal.remove(); }
                }
            """)
            time.sleep(1.0) # 広告が消えるのを少し待つ

            # 広告が消えた後の画面キャプチャを表示
            img_bytes = page.screenshot()
            debug_container.image(img_bytes, caption="【デバッグ】広告除去後のクリーンな画面")

            # 「PSA10」のボタンを探してクリック
            psa10_label = page.locator("label:has-text('PSA10')")
            label_count = psa10_label.count()
            debug_container.write(f"📊 画面内の「PSA10」ボタン発見数: {label_count}")

            if label_count > 0:
                # 念のためforce=Trueで確実にクリック
                psa10_label.first.click(force=True)
                debug_container.success("🎯 PSA10の選択に成功。データ更新待ち...")
                time.sleep(3.0)
                
                # PSA10選択後の画面
                img_bytes_after = page.screenshot()
                debug_container.image(img_bytes_after, caption="【デバッグ】PSA10選択後の画面")
            else:
                debug_container.error("❌ PSA10ボタンが見つかりません。")
                browser.close()
                return "なし"

            # グラフ内部のJavaScript（Highcharts）データを抽出
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

        debug_container.write(f"📈 グラフデータ抽出結果: {chart_data}")

        # グラフからデータが取れた場合
        if chart_data:
            valid_prices = [int(p) for p in chart_data if p is not None and (str(p).isdigit() or isinstance(p, (int, float)))]
            if valid_prices:
                recent_prices = valid_prices[-6:]
                debug_container.write(f"💰 直近6件の価格: {recent_prices}")
                return int(median(recent_prices))

        # グラフデータがNoneだった場合のバックアップ（HTML全検索）
        debug_container.warning("⚠️ グラフデータが直接読めないため、PSA10に絞り込まれたHTMLから数値を全検索します。")
        soup = BeautifulSoup(html, "html.parser")
        raw_prices = []
        
        # グラフのツールチップや軸のテキスト、一覧から金額を回収
        for text in soup.find_all(text=True):
            clean_text = text.strip()
            if "¥" in clean_text or "," in clean_text:
                num_str = re.sub(r"[^\d]", "", clean_text)
                if num_str.isdigit() and len(num_str) >= 3:
                    val = int(num_str)
                    # 適正な価格帯（500円以上、1000万円未満）のみ
                    if 500 < val < 10000000:
                        raw_prices.append(val)

        debug_container.write(f"🔍 検出された価格リスト: {raw_prices[:15]}")
        if raw_prices:
            # 重複を排除し、新しいデータ（末尾）から6件の中央値
            return int(median(raw_prices[-6:]))

        return "なし"

    except Exception as e:
        debug_container.error(f"🚨 エラー詳細: {str(e)}")
        return "なし"

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（広告除去版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（1ページ限定検証モード）")
st.write("海外発送広告（Buyee）を強制排除して、PSA10の価格を特定します。")

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

    # 最初の3つの商品で、広告が消えてPSA10の金額が綺麗に抜けるかテスト
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

    st.success("テスト処理完了！広告除去後のクリーンな画面キャプチャと、正しく絞り込まれた価格データをご確認ください。")
