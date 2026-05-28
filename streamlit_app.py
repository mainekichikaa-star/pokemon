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
# 価格＆開いたページのURL取得ロジック（同一コンテキスト仕様）
# =========================================

def scrape_individual_page(context, product_url):
    """メインと同一のブラウザ環境（セッション保持）の別タブで個別ページを解析する"""
    target_median = "なし"
    final_url = product_url

    try:
        page = context.new_page()
        page.goto(product_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.0)

        # 個別進入時の実測URL（パラメータやsales-historiesを排除）
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

        # 「PSA10」のボタンを強制クリック
        psa10_label = page.locator("label:has-text('PSA10')")
        if psa10_label.count() > 0:
            psa10_label.first.click(force=True)
            time.sleep(1.2)

        html = page.content()
        page.close()  # 個別タブを閉じる

        soup = BeautifulSoup(html, "html.parser")
        psa10_prices = []
        history_items = soup.select("ul.sales-history.item-list li")

        for item in history_items:
            size_elem = item.select_one("p.size")
            price_elem = item.select_one("p.price")

            if size_elem and price_elem:
                if "PSA10" in size_elem.get_text(strip=True):
                    clean_price = int(re.sub(r"[^\d]", "", price_elem.get_text(strip=True)))
                    psa10_prices.append(clean_price)

        if psa10_prices:
            recent_6_prices = psa10_prices[:6]
            target_median = int(median(recent_6_prices))

    except Exception as e:
        try:
            page.close()
        except:
            pass

    return target_median, final_url

# =========================================
# Streamlit UI & 状態（レジュメ）管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（完全統合版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（無限全ページ巡回・実測URL反映）")
st.write("6ページ目の巻き戻りバグを駆逐し、キャッシュ起因のズレや途中停止からの再開に対応した決定版です。")

if "running" not in st.session_state:
    st.session_state.running = False
if "current_page" not in st.session_state:
    st.session_state.current_page = 1  # 内部記録用

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔄 最初から（1ページ目）更新を開始", type="primary", disabled=st.session_state.running):
        st.session_state.current_page = 1
        st.session_state.running = True
        st.rerun()

with col2:
    resume_label = f"▶️ 続きから再開（現在: {st.session_state.current_page}ページ目）"
    if st.button(resume_label, type="secondary", disabled=st.session_state.running or st.session_state.current_page == 1):
        st.session_state.running = True
        st.rerun()

with col3:
    if st.button("🛑 処理を停止する", type="secondary", disabled=not st.session_state.running):
        st.session_state.running = False
        st.warning("🛑 停止要請を受け付けました。現在のページの同期完了後に安全に停止します...")
        st.rerun()

# =========================================
# メイン巡回ループ（Playwright 統一運用）
# =========================================

if st.session_state.running:
    sheet = get_sheet()
    
    st.info("📊 最新データを取得するため、Googleシートをスキャン中...")
    existing_rows = sheet.get_all_values()
    
    current_total_rows = len(existing_rows)
    pokemon_map = {}

    if existing_rows:
        for idx, row in enumerate(existing_rows[1:], start=2):
            while len(row) < 7:
                row.append("")
            key = f"{row[0]}_{row[1]}_{row[2]}"
            pokemon_map[key] = {"row_num": idx}

    log_area = st.empty()
    progress_bar = st.progress(0)
    processed_in_this_run = set()

    # 全リクエストを同一のPlaywrightセッションに集約して人間判定を突破
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
        main_page = context.new_page()

        while st.session_state.running:
            page_num = st.session_state.current_page
            log_area.markdown(f"## 📄 現在、一覧の **ページ {page_num}** を解析中...")
            
            url = (
                f"https://snkrdunk.com/search?"
                f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
                f"&searchCategoryIds=6%2F33"
                f"&brandIds=pokemon"
                f"&page={page_num}"
            )

            try:
                main_page.goto(url, wait_until="networkidle", timeout=45000)
                time.sleep(1.5)
                list_html = main_page.content()
            except Exception as e:
                st.error(f"❌ 一覧取得で通信エラーが発生しました(Page {page_num}): {e}")
                st.session_state.running = False
                break

            # 最終ページ判定（404テキストチェック）
            if "指定されたページが見つかりません" in list_html or main_page.locator("text=商品が見つかりませんでした").count() > 0:
                st.success(f"🎉 最終ページに到達したため、全巡回を完了しました！（最終: {page_num-1}ページ）")
                st.session_state.current_page = 1
                st.session_state.running = False
                break

            matches = re.findall(r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ', list_html)

            if not matches:
                st.success(f"🎉 商品が見つからなくなったため、全ページの巡回を完了しました！（合計: {page_num-1}ページ走破）")
                st.session_state.current_page = 1
                st.session_state.running = False
                break

            matches = matches[:30]
            new_rows = []
            total_items = len(matches)

            for idx, match in enumerate(matches):
                if not st.session_state.running:
                    break

                href = match[0]
                full_title = match[1]

                # 先頭直後の正しい商品固有ID（5桁前後）を安全に抽出する正規表現
                id_match = re.search(r'/(?:products|apparels)/(?:used/)?(\d+)', href)
                if not id_match:
                    continue
                
                card_id = id_match.group(1)
                access_url = f"https://snkrdunk.com/apparels/{card_id}"

                name, rarity, card_no, pack = parse_title(full_title)
                
                run_key = f"{name}_{rarity}_{card_no}"
                if run_key in processed_in_this_run:
                    continue
                processed_in_this_run.add(run_key)

                progress_bar.progress((idx + 1) / total_items)
                log_area.markdown(f"### 🔄 処理中({idx+1}/{total_items}件目): **{name}** (ページ {page_num})")

                # クッキー・セッションを引き継いだコンテキスト別タブで個別ページ取得
                psa_price, real_product_url = scrape_individual_page(context, access_url)
                
                now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
                row_data = [name, rarity, card_no, pack, psa_price, now_str, real_product_url]

                # Googleスプレッドシート重複・位置ズレ連動チェック
                if run_key in pokemon_map:
                    row_num = pokemon_map[run_key]["row_num"]
                    sheet.update(f"A{row_num}:G{row_num}", [row_data])
                    st.toast(f"✏️ 【上書き更新】{name} -> ¥{psa_price}")
                else:
                    new_rows.append(row_data)
                    st.toast(f"➕ 【新規追加】{name} -> ¥{psa_price}")
                    
                    # 内部マップに行数を同期させることで、手動操作が挟まらない限り絶対位置を維持
                    current_total_rows += 1
                    pokemon_map[run_key] = {"row_num": current_total_rows}

                time.sleep(random.uniform(2.5, 4.0))

            # 1ページ処理完了毎に新規カードを一括追加
            if new_rows and st.session_state.running:
                sheet.append_rows(new_rows)

            if st.session_state.running:
                st.session_state.current_page += 1
                time.sleep(random.uniform(4.0, 6.0))  # ページ遷移時は人間らしく少し長めにディレイ
            else:
                st.warning(f"🛑 {page_num}ページ目の同期処理を完了し、安全に停止しました。")
                break

        browser.close()

    st.session_state.running = False
    st.rerun()
