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
# 価格＆開いたページのURL取得ロジック（ハイブリッド版）
# =========================================

def get_psa10_data_from_page(page, product_url):
    target_median = "なし"
    final_url = product_url

    try:
        page.goto(product_url, wait_until="networkidle", timeout=45000)
        time.sleep(1.0)

        final_url = page.url.split("/sales-histories")[0].split("?")[0]

        try:
            page.evaluate("""
                () => {
                    const closeBtn = document.querySelector('.buyee-bcF-modal-close') || document.querySelector('[class*="modal-close"]');
                    if (closeBtn) { closeBtn.click(); return; }
                    const modal = document.getElementById('buyee-bcSection') || document.querySelector('.buyee-bcF-modal');
                    if (modal) { modal.remove(); }
                }
            """)
            time.sleep(0.3)
        except:
            pass

        psa10_label = page.locator("label:has-text('PSA10')")
        if psa10_label.count() > 0:
            psa10_label.first.click(force=True)
            time.sleep(1.2)

        html_content = page.content()
        soup = BeautifulSoup(html_content, "html.parser")
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

    except Exception as e:
        pass

    if target_median == "なし":
        history_url = f"{final_url}/sales-histories?slide=right"
        try:
            page.goto(history_url, wait_until="load", timeout=45000)
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2.0)

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

            for i in range(15):
                h2_exists = page.evaluate("""
                    () => {
                        const headings = Array.from(document.querySelectorAll('h2'));
                        return headings.some(h => /.*10.*/.test(h.textContent));
                    }
                """)
                
                if h2_exists:
                    try:
                        page.evaluate("""
                            () => {
                                const headings = Array.from(document.querySelectorAll('h2'));
                                const target = headings.find(h => /.*10.*/.test(h.textContent));
                                if (target) { target.scrollIntoView(); }
                            }
                        """)
                        time.sleep(1.0)
                        page.wait_for_selector("ul.sales-history li", timeout=5000)
                        time.sleep(0.5)
                    except:
                        pass
                    break
                
                page.evaluate("window.scrollBy(0, 500)")
                time.sleep(0.6)

            page.evaluate("window.scrollBy(0, 200)")
            time.sleep(1.0)

            html_content = page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            psa10_prices = []
            
            h2_tags = soup.find_all(["h2", "div"], class_=lambda x: x and 'title' in x)
            if not h2_tags:
                h2_tags = soup.find_all("h2")
                
            psa10_ul = None
            for h2 in h2_tags:
                if re.search(r'10', h2.get_text()):
                    psa10_ul = h2.find_next("ul", class_=lambda x: x and 'sales-history' in x)
                    break
            
            if psa10_ul:
                history_items = psa10_ul.select("li")
                for item in history_items:
                    size_elem = item.select_one("p[class*='size'], p.size")
                    price_elem = item.select_one("p[class*='price'], p.price")

                    if size_elem and price_elem:
                        size_text = size_elem.get_text(strip=True)
                        price_text = price_elem.get_text(strip=True)

                        if "10" in size_text:
                            if re.sub(r"[^\d]", "", price_text):
                                clean_price = int(re.sub(r"[^\d]", "", price_text))
                                psa10_prices.append(clean_price)

            if not psa10_prices:
                all_lists = soup.find_all("ul", class_=lambda x: x and 'sales-history' in x)
                for ul in all_lists:
                    items = ul.select("li")
                    for item in items:
                        size_elem = item.find("p", class_=lambda x: x and 'size' in x)
                        price_elem = item.find("p", class_=lambda x: x and 'price' in x)
                        if size_elem and price_elem:
                            size_text = size_elem.get_text(strip=True)
                            price_text = price_elem.get_text(strip=True)
                            if "10" in size_text and not "状態" in size_text:
                                digits = re.sub(r"[^\d]", "", price_text)
                                if digits:
                                    psa10_prices.append(int(digits))

            if psa10_prices:
                recent_6_prices = psa10_prices[:6]
                target_median = int(median(recent_6_prices))

        except Exception as e:
            pass

    return target_median, final_url

# =========================================
# Streamlit UI & 状態管理
# =========================================

st.set_page_config(page_title="ポケカ価格自動反映（超安定版）", layout="wide")
st.title("🃏 ポケカ価格自動反映ツール（超安定版）")
st.write("表記の揺れ（状態10 / PSA10）や、HTMLの構造変化によるすり抜けを防止する予備ロジックを組み込んだ最強安定化版です。")

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

log_area = st.empty()
progress_bar = st.progress(0)
status_text = st.empty()

# =========================================
# メイン巡回ループ
# =========================================

if st.session_state.running:
    sheet = get_sheet()
    
    st.info("📊 最新データを取得するため、Googleシートをスキャン中...")
    raw_rows = sheet.get_all_values()
    
    pokemon_map = {}
    
    if raw_rows and len(raw_rows) > 1:
        header = raw_rows[0]
        data_rows = raw_rows[1:]
        
        # 名前(0), 品番(2), 収録パック(3) の組み合わせで重複行を検知し統合する
        unique_data = {}
        has_duplicates = False
        
        for idx, row in enumerate(data_rows, start=2):
            while len(row) < 7:
                row.append("")
            # 判定キーを「名前_品番_収録パック」に変更
            key = f"{row[0]}_{row[2]}_{row[3]}"
            
            # 既存のキーがある場合は重複（新しいデータで上書き＝行削除が必要）
            if key in unique_data:
                has_duplicates = True
            unique_data[key] = row

        # 重複が存在した場合はシート側を一旦綺麗にまとめる
        if has_duplicates:
            st.warning("🧹 シート内に重複データを検知しました。1つにまとめてクリーンアップしています...")
            sheet.clear()
            sheet.append_row(header)
            if unique_data:
                sheet.append_rows(list(unique_data.values()))
            # 再取得してインデックスを正常化
            existing_rows = sheet.get_all_values()
        else:
            existing_rows = raw_rows

        # インデックス構築
        for idx, row in enumerate(existing_rows[1:], start=2):
            while len(row) < 7:
                row.append("")
            key = f"{row[0]}_{row[2]}_{row[3]}"
            pokemon_map[key] = {
                "row_num": idx,
                "old_price": row[4]
            }
            
    current_total_rows = len(sheet.get_all_values())
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
                
                # 巡回中の重複排除キーも「名前_品番_収録パック」に変更
                run_key = f"{name}_{card_no}_{pack}"
                if run_key in processed_in_this_run:
                    continue
                processed_in_this_run.add(run_key)

                progress_bar.progress((idx + 1) / total_items)
                status_text.write(f"🔄 処理中({idx+1}/{total_items}件目): **{name}**")

                psa_price, real_product_url = get_psa10_data_from_page(page, access_url)
                
                now_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y/%m/%d %H:%M:%S")

                if run_key in pokemon_map:
                    row_num = pokemon_map[run_key]["row_num"]
                    old_price = pokemon_map[run_key]["old_price"]
                    
                    if psa_price == "なし" and old_price not in ["なし", "", None]:
                        st.toast(f"⚠️ 【維持】{name} の現在の価格が「なし」のため、既存の価格（¥{old_price}）を保護しました")
                    else:
                        row_data = [name, rarity, card_no, pack, psa_price, now_str, real_product_url]
                        sheet.update(f"A{row_num}:G{row_num}", [row_data])
                        pokemon_map[run_key]["old_price"] = psa_price
                        
                        if psa_price != "なし":
                            st.toast(f"✏️ 【上書き更新】{name} -> ¥{psa_price:,}")
                        else:
                            st.toast(f"✏️ 【上書き更新】{name} -> 価格データなし")
                else:
                    row_data = [name, rarity, card_no, pack, psa_price, now_str, real_product_url]
                    new_rows.append(row_data)
                    if psa_price != "なし":
                        st.toast(f"➕ 【新規追加】{name} -> ¥{psa_price:,}")
                    else:
                        st.toast(f"➕ 【新規追加】{name} -> 価格データなし")
                    current_total_rows += 1
                    pokemon_map[run_key] = {"row_num": current_total_rows, "old_price": psa_price}

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
