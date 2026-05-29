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
# 価格＆URL取得ロジック（リダイレクト・タブクリック対策版）
# =========================================

def get_psa10_data_from_page(page, product_url):
    """個別商品ページへのリダイレクトを阻止し、売買履歴タブを強制発火させるロジック"""
    target_median = "なし"
    
    # 確実に親の履歴URLを構築
    clean_url = product_url.split("/used/")[0].split("/sales-histories")[0].split("?")[0]
    history_url = f"{clean_url}/sales-histories?slide=right"

    try:
        # 1. ページ遷移（ネットワークが完全に落ち着くまでしっかり待機）
        page.goto(history_url, wait_until="networkidle", timeout=45000)
        time.sleep(2.5)

        # 2. 個別個体URL（/used/xxxx）に飛ばされていた場合は、再度履歴URLへ強制引き戻し
        if "/used/" in page.url:
            page.goto(history_url, wait_until="load", timeout=30000)
            time.sleep(2.0)

        # 3. モーダルやポップアップの徹底排除
        page.evaluate("""
            () => {
                const closeSelectors = ['.buyee-bcF-modal-close', '[class*="modal-close"]', '.close-button'];
                closeSelectors.forEach(s => {
                    const btn = document.querySelector(s);
                    if (btn) btn.click();
                });
                const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                if (modal) modal.remove();
            }
        """)

        # 4. 【超重要】非同期で隠れている「売買履歴」タブやボタンをJavaScriptで強制クリック
        page.evaluate("""
            () => {
                // ボタン名に「履歴」や「売買」を含むタブを網羅してクリック発火
                const tabs = Array.from(document.querySelectorAll('button, li, a, div'));
                const historyTab = tabs.find(el => /売買履歴|履歴/.test(el.textContent));
                if (historyTab) {
                    historyTab.click();
                }
            }
        """)
        time.sleep(1.5)

        # 5. 表記揺れ（状態10 / PSA10）を狙った追跡スクロール
        for i in range(12):
            h2_exists = page.evaluate("""
                () => {
                    const headings = Array.from(document.querySelectorAll('h2, div[class*="title"]'));
                    return headings.some(h => /10/.test(h.textContent));
                }
            """)
            
            if h2_exists:
                page.evaluate("""
                    () => {
                        const headings = Array.from(document.querySelectorAll('h2, div[class*="title"]'));
                        const target = headings.find(h => /10/.test(h.textContent));
                        if (target) target.scrollIntoView({behavior: 'smooth', block: 'center'});
                    }
                """)
                time.sleep(1.5)  # 描画通信ウェイト
                break
            
            page.evaluate("window.scrollBy(0, 450)")
            time.sleep(0.6)

        # 念のための最終レンダリング確定ウェイト
        page.evaluate("window.scrollBy(0, 150)")
        time.sleep(1.0)

        # 6. HTML解析
        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        psa10_prices = []
        
        # --- メイン：10を含む見出しセクションのリストから取得 ---
        sections = soup.find_all(["h2", "div", "p"], class_=lambda x: x and ('title' in x or 'hd' in x))
        if not sections:
            sections = soup.find_all("h2")

        psa10_ul = None
        for sec in sections:
            if "10" in sec.get_text():
                psa10_ul = sec.find_next("ul", class_=lambda x: x and 'sales-history' in x)
                if psa10_ul:
                    break
        
        if psa10_ul:
            for item in psa10_ul.select("li"):
                size_elem = item.select_one("p[class*='size'], p.size")
                price_elem = item.select_one("p[class*='price'], p.price")
                if size_elem and price_elem:
                    size_text = size_elem.get_text(strip=True)
                    price_text = price_elem.get_text(strip=True)
                    if "10" in size_text:
                        digits = re.sub(r"[^\d]", "", price_text)
                        if digits:
                            psa10_prices.append(int(digits))

        # --- バックアップ：見出し構造が変わっていてもページ内の全リストから「10」を全回収 ---
        if not psa10_prices:
            all_uls = soup.find_all("ul", class_=lambda x: x and 'sales-history' in x)
            for ul in all_uls:
                for item in ul.select("li"):
                    size_elem = item.find("p", class_=lambda x: x and 'size' in x)
                    price_elem = item.find("p", class_=lambda x: x and 'price' in x)
                    if size_elem and price_elem:
                        size_text = size_elem.get_text(strip=True)
                        price_text = price_elem.get_text(strip=True)
                        if "10" in size_text and not "状態" in size_text:
                            digits = re.sub(r"[^\d]", "", price_text)
                            if digits:
                                psa10_prices.append(int(digits))

        # 中央値算出
        if psa10_prices:
            recent_6 = psa10_prices[:6]
            target_median = int(median(recent_6))

    except Exception as e:
        pass

    return target_median, clean_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（構造追従版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（構造追従版）")
st.write("中古個別ページへの強制リダイレクトを自動検知し、非同期の売買履歴タブをプログラムが直接クリックして強制抽出し直す最新版です。")

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
    st.warning("🛑 停止要請を受け付けました。安全に停止します...")
    st.rerun()

log_area = st.empty()
progress_bar = st.progress(0)
status_text = st.empty()

# =========================================
# メイン巡回ループ
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
    
    processed_in_this_run = set()

    while st.session_state.running:
        current_page = st.session_state.current_page
        log_area.markdown(f"## 📄 現在、一覧の **ページ {current_page}** を解析中...")
        
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
            st.success(f"🎉 全巡回を完了しました！（最終: {current_page-1}ページ）")
            st.session_state.current_page = 1
            st.session_state.running = False
            break
        elif res.status_code != 200:
            st.error(f"❌ ページの取得に失敗しました。Status: {res.status_code}")
            st.session_state.running = False
            break

        matches = re.findall(r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ', res.text)

        if not matches:
            st.success(f"🎉 全ページの巡回を完了しました！（合計: {current_page-1}ページ走破）")
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
                viewport={"width": 1280, "height": 1000},
                locale="ja-JP"
            )
            page = context.new_page()

            for idx, match in enumerate(matches):
                if not st.session_state.running:
                    break

                href = match[0]
                full_title = match[1]

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
                status_text.write(f"🔄 処理中({idx+1}/{total_items}件目): **{name}**")

                psa_price, real_product_url = get_psa10_data_from_page(page, access_url)
                
                now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
                row_data = [name, rarity, card_no, pack, psa_price, now_str, real_product_url]

                if run_key in pokemon_map:
                    row_num = pokemon_map[run_key]["row_num"]
                    sheet.update(f"A{row_num}:G{row_num}", [row_data])
                    if psa_price != "なし":
                        st.toast(f"✏️ 【上書き更新】{name} -> ¥{psa_price:,}")
                    else:
                        st.toast(f"✏️ 【上書き更新】{name} -> 価格データなし")
                else:
                    new_rows.append(row_data)
                    if psa_price != "なし":
                        st.toast(f"➕ 【新規追加】{name} -> ¥{psa_price:,}")
                    else:
                        st.toast(f"➕ 【新規追加】{name} -> 価格データなし")
                    current_total_rows += 1
                    pokemon_map[run_key] = {"row_num": current_total_rows}

                time.sleep(random.uniform(3.5, 5.5))

            browser.close()

        if new_rows and st.session_state.running:
            sheet.append_rows(new_rows)

        if st.session_state.running:
            st.session_state.current_page += 1
            time.sleep(3.5)
            st.rerun() 
        else:
            st.warning(f"🛑 ユーザーにより停止されました。（前回処理完了: {current_page} ページ目）")
            break

    st.session_state.running = False
    st.rerun()
