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
# 価格取得（売買履歴解析ロジック）
# =========================================

def get_psa10_price(product_url, log_container):
    base_url = product_url.split("/sales-histories")[0].split("?")[0]

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
            time.sleep(1.5)

            # Buyeeポップアップの消去処理
            page.evaluate("""
                () => {
                    const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                    if (closeBtn) { closeBtn.click(); return; }
                    
                    const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                    if (modal) { modal.remove(); }
                }
            """)
            time.sleep(0.5)

            # 「PSA10」のボタンを強制クリック
            psa10_label = page.locator("label:has-text('PSA10')")
            if psa10_label.count() > 0:
                psa10_label.first.click(force=True)
                time.sleep(1.5)

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        psa10_prices = []

        history_items = soup.select("ul.sales-history.item-list li")

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
            return target_median
        else:
            return "なし"

    except Exception as e:
        return "なし"

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（無限巡回版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（無限全ページ巡回）")
st.write("実際のページネーションURL構造に完全適合させ、ひたすら全ページを走破します。")

if "running" not in st.session_state:
    st.session_state.running = False

col1, col2 = st.columns(2)

with col1:
    start_button = st.button("🔄 全ページ更新を開始", type="primary", disabled=st.session_state.running)
with col2:
    stop_button = st.button("🛑 処理を停止する", type="secondary", disabled=not st.session_state.running)

if start_button:
    st.session_state.running = True
    st.rerun()

if stop_button:
    st.session_state.running = False
    st.warning("🛑 停止要協を受け付けました。現在のカードの同期完了後に安全に停止します...")
    st.rerun()

# =========================================
# メイン巡回ループ
# =========================================

if st.session_state.running:
    sheet = get_sheet()
    
    st.info("📊 重複チェックのため、既存のGoogleシートをスキャン中...")
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
    processed_in_this_run = set()  # 今回のセッション内での多重重複防止

    while st.session_state.running:
        log_area.markdown(f"## 📄 現在、一覧の **ページ {current_page}** を解析中...")
        
        # 【重要】ご提示いただいたHTMLの正規URL構造に完全一致させました
        url = (
            f"https://snkrdunk.com/search?"
            f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
            f"&searchCategoryIds=6%2F33"
            f"&brandIds=pokemon"
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
            st.session_state.running = False
            break
        elif res.status_code != 200:
            st.error(f"❌ ページの取得に失敗しました。Status: {res.status_code}")
            st.session_state.running = False
            break

        # 商品リンクとタイトルを取得
        matches = re.findall(r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ', res.text)

        if not matches:
            st.success(f"🎉 商品が見つからなくなったため、全ページの巡回を完了しました！（合計: {current_page-1}ページ走破）")
            st.session_state.running = False
            break

        new_rows = []
        total_items = len(matches)

        for idx, match in enumerate(matches):
            # ループのステップ毎に停止ボタンのフラグをチェック
            if not st.session_state.running:
                break

            href = match[0]
            full_title = match[1]

            clean_path = href.split("?")[0].replace("/products/", "/apparels/").replace("/used", "")
            product_url = clean_path if clean_path.startswith("http") else "https://snkrdunk.com" + clean_path

            name, rarity, card_no, pack = parse_title(full_title)
            
            # 同一ページ内、または今回すでに処理したカードの重複上書き回数を減らすガード
            run_key = f"{name}_{rarity}_{card_no}"
            if run_key in processed_in_this_run:
                continue
            processed_in_this_run.add(run_key)

            progress_bar.progress((idx + 1) / total_items)
            log_area.markdown(f"### 🔄 処理中({idx+1}/{total_items}件目): **{name}** (ページ {current_page})")

            # 個別ページの売買履歴からPSA10中央値を取得
            psa_price = get_psa10_price(product_url, st)
            
            now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
            row_data = [name, rarity, card_no, pack, psa_price, now_str, product_url]

            # Googleスプレッドシート重複チェック（あれば上書き、なければ新規）
            if run_key in pokemon_map:
                row_num = pokemon_map[run_key]["row_num"]
                sheet.update(f"A{row_num}:G{row_num}", [row_data])
                st.toast(f"✏️ 【上書き更新】{name} -> ¥{psa_price}")
            else:
                new_rows.append(row_data)
                st.toast(f"➕ 【新規追加】{name} -> ¥{psa_price}")
                # 今回追加したものを配列ベースでマップに仮登録（この後の重複をさらに防ぐ）
                pokemon_map[run_key] = {"row_num": len(existing_rows) + len(new_rows) + 1}

            # 相手方サーバーへの不可軽減のためランダムなウェイト
            time.sleep(random.uniform(2.5, 4.0))

        # 新規取得カードがあればページごとに一括挿入して書き込み速度を最適化
        if new_rows and st.session_state.running:
            sheet.append_rows(new_rows)
            # 反映が済んだらexisting_rowsのトータル行数を同期
            existing_rows.extend(new_rows)

        if st.session_state.running:
            current_page += 1
            time.sleep(2.0)
        else:
            st.warning("🛑 処理がユーザーにより停止されました。")
            break

    st.session_state.running = False
    st.rerun()
