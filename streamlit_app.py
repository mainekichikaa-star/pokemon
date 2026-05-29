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
SHEET_NAME = "カード（PSA10）"  # 【変更】指定のシート名

HEADERS = [
    "名前",
    "レアリティ",
    "品番",          # 【変更】型番から「品番」へ
    "収録パック",
    "現在の価格",    # 【変更】指定の項目名へ
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
    # 【対策】HTML特殊文字（&amp;など）を通常の文字（&など）にデコード
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
# 価格＆開いたページのURL取得ロジック（PSA10相場特化版）
# =========================================

def get_psa10_data_from_page(page, product_url):
    """相場ページ（/sales-histories）へ直接アクセスして、PSA10の直近売買履歴を取得する"""
    target_median = "なし"
    # 実測URLのベースを作る
    final_url = product_url.split("/sales-histories")[0].split("?")[0]
    
    # 状態ごとの相場ページURLへ直接書き換え
    history_url = f"{final_url}/sales-histories?slide=right"

    try:
        page.goto(history_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.0)

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

        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        psa10_prices = []
        
        # 相場専用ページ内の売買履歴リストを取得
        history_items = soup.select("ul.sales-history.item-list li")

        for item in history_items:
            size_elem = item.select_one("p.size")
            price_elem = item.select_one("p.price")

            if size_elem and price_elem:
                size_text = size_elem.get_text(strip=True)
                price_text = price_elem.get_text(strip=True)

                # 「PSA10」の表記がある履歴の価格のみを抽出
                if "PSA10" in size_text:
                    clean_price = int(re.sub(r"[^\d]", "", price_text))
                    psa10_prices.append(clean_price)

        # 直近6件の売買履歴から中央値を算出
        if psa10_prices:
            recent_6_prices = psa10_prices[:6]
            target_median = int(median(recent_6_prices))

    except Exception as e:
        pass

    return target_median, final_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（最新版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（無限全ページ巡回・実測URL反映）")
st.write("文字化けを解消し、PSA10の相場ページから確実に履歴価格を引っこ抜く完全版です。")

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

        # 1ページ最大30商品制限
        matches = matches[:30]

        new_rows = []
        total_items = len(matches)

        # 1ページ分（30商品）を一気に回すため、ブラウザをここで1回だけ立ち上げる
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
                full_title = match[1]

                # 先頭直後の正しい5桁IDだけをピンポイント抽出
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
                log_area.markdown(f"### 🔄 処理中({idx+1}/{total_items}件目): **{name}** (ページ {current_page})")

                # 修正したPSA10相場ページ巡回ロジックを呼び出し
                psa_price, real_product_url = get_psa10_data_from_page(page, access_url)
                
                now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")
                row_data = [name, rarity, card_no, pack, psa_price, now_str, real_product_url]

                # Googleスプレッドシート重複・キャッシュチェック
                if run_key in pokemon_map:
                    row_num = pokemon_map[run_key]["row_num"]
                    sheet.update(f"A{row_num}:G{row_num}", [row_data])
                    st.toast(f"✏️ 【上書き更新】{name} -> ¥{psa_price}")
                else:
                    # 新規追加データをリストに追加
                    new_rows.append(row_data)
                    st.toast(f"➕ 【新規追加】{name} -> ¥{psa_price}")
                    
                    # 【キャッシュ位置ズレ対策】
                    current_total_rows += 1
                    pokemon_map[run_key] = {"row_num": current_total_rows}

                time.sleep(random.uniform(2.5, 4.0))

            browser.close() # 1ページ（30件）終わったらブラウザを安全に終了

        # 新規取得カードの一括挿入
        if new_rows and st.session_state.running:
            sheet.append_rows(new_rows)

        if st.session_state.running:
            st.session_state.current_page += 1  # ページカウントを安全に進める
            time.sleep(3.5)
        else:
            st.warning(f"🛑 ユーザーにより停止されました。（前回処理完了: {current_page} ページ目）")
            break

    st.session_state.running = False
    st.rerun()
