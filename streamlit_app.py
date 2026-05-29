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
# 価格＆開いたページのURL取得ロジック（描画遅延バグ修正版）
# =========================================

def get_psa10_data_from_page(page, product_url, card_name):
    """他状態の長さに負けないよう、スクロールしながらPSA10の要素を追跡する（安定化版）"""
    target_median = "なし"
    final_url = product_url.split("/sales-histories")[0].split("?")[0]
    history_url = f"{final_url}/sales-histories?slide=right"
    
    shot_before = None
    shot_after = None
    error_msg = ""

    try:
        # 確実にページ遷移を待機
        page.goto(history_url, wait_until="load", timeout=45000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(1.5)

        # Buyeeポップアップの消去
        try:
            page.evaluate("""
                () => {
                    const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                    if (closeBtn) { closeBtn.click(); return; }
                    const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                    if (modal) { modal.remove(); }
                }
            """)
            time.sleep(0.5)
        except:
            pass

        # 1回目の撮影（アクセス＆ポップアップ処理直後）
        try:
            shot_before = page.screenshot(full_page=False, timeout=5000)
        except Exception as e:
            shot_before = f"直後スクショ撮影エラー: {str(e)}"

        # 「状態PSA10の売買履歴」の見出しがあるかスクロールしながら追跡
        found_psa10 = False
        for i in range(12):  
            h2_exists = page.evaluate("""
                () => {
                    const headings = Array.from(document.querySelectorAll('h2.size-title'));
                    return headings.some(h => h.textContent.includes('状態PSA10の売買履歴'));
                }
            """)
            
            if h2_exists:
                try:
                    # 見つかったらフォーカスして微調整
                    locator = page.locator("h2.size-title:has-text('状態PSA10の売買履歴')")
                    locator.scroll_into_view_if_needed()
                    found_psa10 = True
                    time.sleep(0.5)
                    
                    # 【重要修正】見出しが見つかった後、リスト内の実際の要素が描画されるまで最大5秒待つ
                    page.wait_for_selector("ul.sales-history.item-list li.used", timeout=5000)
                    time.sleep(0.5)
                except Exception as ex:
                    # 待機に失敗した場合はエラーログに残す
                    error_msg = f"PSA10のリスト要素の描画待機に失敗しました: {str(ex)}"
                break
            
            # 見つからなければ少しずつ下へスクロール
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(0.8)

        # 2回目の撮影（スクロール＆描画待機完了後）
        try:
            shot_after = page.screenshot(full_page=False, timeout=5000)
        except Exception as e:
            shot_after = f"追跡後スクショ撮影エラー: {str(e)}"

        # 解析処理
        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        psa10_prices = []
        
        h2_tags = soup.find_all("h2", class_="size-title")
        psa10_ul = None
        
        for h2 in h2_tags:
            if "状態PSA10の売買履歴" in h2.get_text():
                # class名に「item-list」を含むulを確実に探す
                psa10_ul = h2.find_next("ul", class_=lambda x: x and 'sales-history' in x and 'item-list' in x)
                break
        
        if psa10_ul:
            history_items = psa10_ul.select("li.used")
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
            error_msg = "" # 成功した場合はエラーメッセージをクリア
        else:
            if not error_msg: # 既に待機エラーなどが起きていない場合のみ上書き
                if not found_psa10:
                    error_msg = "ページ内に「状態PSA10の売買履歴」の見出し自体が見つかりませんでした。"
                else:
                    error_msg = "PSA10の見出しはありましたが、リスト内の実際の売買データがHTML取得時点で空でした（同期ズレ）。"

    except Exception as e:
        error_msg = f"ブラウザ処理中に致命的エラーが発生: {str(e)}"

    # セッション状態にスクショ履歴を追加保存
    if "screenshot_history" not in st.session_state:
        st.session_state.screenshot_history = []
        
    st.session_state.screenshot_history.append({
        "name": card_name,
        "url": history_url,
        "price": target_median,
        "shot_before": shot_before,
        "shot_after": shot_after,
        "error": error_msg,
        "time": datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%H:%M:%S")
    })

    return target_median, final_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（同期バグ修正版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（描画遅延バグ修正版）")
st.write("スクロール後のデータ描画待ちを強化し、HTMLから確実に売買データを抽出できるように修正しました。")

if "running" not in st.session_state:
    st.session_state.running = False
if "current_page" not in st.session_state:
    st.session_state.current_page = 1
if "screenshot_history" not in st.session_state:
    st.session_state.screenshot_history = []

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔄 最初から（1ページ目）更新を開始", type="primary", disabled=st.session_state.running):
        st.session_state.screenshot_history = []  # 履歴をリセット
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

# 左右2分割
main_col, side_col = st.columns([1, 1])

with main_col:
    log_area = st.empty()
    progress_bar = st.progress(0)
    status_text = st.empty()

with side_col:
    st.write("### 📜 走査した全カードのブラウザ画面履歴")
    sc_container = st.container()
    with sc_container:
        if st.session_state.screenshot_history:
            for item in reversed(st.session_state.screenshot_history):
                st.markdown(f"---")
                st.markdown(f"**【{item['time']}】 {item['name']}**")
                st.markdown(f"結果価格: `¥{item['price']}`  |  [対象URL]({item['url']})")
                
                if item["error"]:
                    st.error(f"❌ {item['error']}")
                else:
                    st.success("✅ 正常にデータを取得できました")
                
                c1, c2 = st.columns(2)
                with c1:
                    if item['shot_before'] and isinstance(item['shot_before'], bytes):
                        st.image(item['shot_before'], caption="📸 開いてすぐ", use_container_width=True)
                    else:
                        st.warning(f"⚠️ {item['shot_before'] if item['shot_before'] else '直後スクショなし'}")
                with c2:
                    if item['shot_after'] and isinstance(item['shot_after'], bytes):
                        st.image(item['shot_after'], caption="📸 PSA10追跡スクロール後", use_container_width=True)
                    else:
                        st.warning(f"⚠️ {item['shot_after'] if item['shot_after'] else '追跡後スクショなし'}")
        else:
            st.info("スタートすると、ここに全カードの処理画面履歴がたまっていきます。")

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

                psa_price, real_product_url = get_psa10_data_from_page(page, access_url, name)
                
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

                time.sleep(random.uniform(3.0, 5.0))

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
