```python
import streamlit as st
import requests
import re
import time
import random
import subprocess
import html
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# =========================================
# Playwright install
# =========================================

@st.cache_resource
def install_playwright_browsers():
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=True
        )
        return True
    except Exception as e:
        st.error(f"Playwrightブラウザのインストール失敗: {e}")
        return False

with st.spinner("システム準備中..."):
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
# 広告・ポップアップ除去
# =========================================

def close_ads_and_popups(page):

    popup_selectors = [
        '[aria-label="Close"]',
        '[aria-label="閉じる"]',
        '.buyee-bcF-modal-close',
        '.modal-close',
        '[class*="close"]',
        '[class*="Close"]',
        'button[aria-label*="close"]',
        'button[aria-label*="閉じ"]',
        '#buyee-bcSection'
    ]

    for selector in popup_selectors:
        try:
            elements = page.locator(selector)

            count = elements.count()

            for i in range(count):
                try:
                    el = elements.nth(i)

                    if el.is_visible(timeout=1000):

                        tag_name = el.evaluate("(e) => e.tagName")

                        if tag_name.lower() == "button":
                            el.click(timeout=1000)

                        else:
                            page.evaluate("""
                                (selector) => {
                                    const el = document.querySelector(selector);
                                    if (el) el.remove();
                                }
                            """, selector)

                        time.sleep(0.3)

                except:
                    pass

        except:
            pass

    # iframe広告削除
    try:
        page.evaluate("""
            () => {

                const iframes = document.querySelectorAll("iframe");

                iframes.forEach(frame => {

                    const src = frame.src || "";

                    if (
                        src.includes("ads") ||
                        src.includes("doubleclick") ||
                        src.includes("googleads") ||
                        src.includes("adservice")
                    ) {
                        frame.remove();
                    }

                });

            }
        """)
    except:
        pass

# =========================================
# PSA10価格取得
# =========================================

def get_psa10_data_from_page(page, product_url):

    target_median = "なし"

    final_url = product_url.split("/sales-histories")[0].split("?")[0]

    history_url = f"{final_url}/sales-histories?slide=right"

    try:

        page.goto(
            history_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_load_state("networkidle")

        time.sleep(2.5)

        # 広告削除
        close_ads_and_popups(page)

        # ページ全体を読み込ませる
        for _ in range(12):

            page.mouse.wheel(0, 1500)

            time.sleep(0.8)

            close_ads_and_popups(page)

        # HTML取得
        html_content = page.content()

        soup = BeautifulSoup(html_content, "html.parser")

        psa10_prices = []

        # =========================================
        # PSA10だけ厳密取得
        # =========================================

        all_li = soup.find_all("li")

        for li in all_li:

            text = li.get_text(" ", strip=True)

            # PSA10だけ
            if not re.search(r'PSA\s*10', text, re.IGNORECASE):
                continue

            # 数字取得
            yen_matches = re.findall(r'¥\s?([\d,]+)', text)

            if not yen_matches:
                yen_matches = re.findall(r'([0-9,]+)\s?円', text)

            for price in yen_matches:

                try:

                    clean_price = int(
                        re.sub(r"[^\d]", "", price)
                    )

                    if clean_price > 100:
                        psa10_prices.append(clean_price)

                except:
                    pass

        # =========================================
        # フォールバック
        # =========================================

        if not psa10_prices:

            sales_lists = soup.select("ul")

            for ul in sales_lists:

                ul_text = ul.get_text(" ", strip=True)

                if "PSA10" not in ul_text.upper():
                    continue

                prices = re.findall(r'¥\s?([\d,]+)', ul_text)

                for price in prices:

                    try:

                        clean_price = int(
                            re.sub(r"[^\d]", "", price)
                        )

                        if clean_price > 100:
                            psa10_prices.append(clean_price)

                    except:
                        pass

        # 重複削除
        psa10_prices = list(dict.fromkeys(psa10_prices))

        # 直近6件中央値
        if psa10_prices:

            recent_prices = psa10_prices[:6]

            target_median = int(
                median(recent_prices)
            )

    except Exception:
        pass

    return target_median, final_url

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(
    page_title="ポケカ価格自動反映",
    layout="wide"
)

st.title("🃏 ポケカ価格自動反映ツール")

if "running" not in st.session_state:
    st.session_state.running = False

if "current_page" not in st.session_state:
    st.session_state.current_page = 1

col1, col2, col3 = st.columns(3)

with col1:

    if st.button(
        "🔄 最初から更新開始",
        type="primary",
        disabled=st.session_state.running
    ):

        st.session_state.current_page = 1
        st.session_state.running = True
        st.rerun()

with col2:

    resume_label = (
        f"▶️ 続きから再開 "
        f"(現在: {st.session_state.current_page}ページ目)"
    )

    if st.button(
        resume_label,
        disabled=st.session_state.running
    ):

        st.session_state.running = True
        st.rerun()

with col3:

    stop_button = st.button(
        "🛑 停止",
        disabled=not st.session_state.running
    )

if stop_button:

    st.session_state.running = False

    st.warning(
        "🛑 停止要求を受け付けました"
    )

    st.rerun()

log_area = st.empty()

progress_bar = st.progress(0)

status_text = st.empty()

# =========================================
# メイン処理
# =========================================

if st.session_state.running:

    sheet = get_sheet()

    existing_rows = sheet.get_all_values()

    current_total_rows = len(existing_rows)

    pokemon_map = {}

    if existing_rows:

        for idx, row in enumerate(existing_rows[1:], start=2):

            while len(row) < 7:
                row.append("")

            key = f"{row[0]}_{row[1]}_{row[2]}"

            pokemon_map[key] = {
                "row_num": idx
            }

    processed_in_this_run = set()

    while st.session_state.running:

        current_page = st.session_state.current_page

        log_area.markdown(
            f"## 📄 ページ {current_page} を解析中..."
        )

        url = (
            f"https://snkrdunk.com/search?"
            f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
            f"&searchCategoryIds=6%2F33"
            f"&brandIds=pokemon"
            f"&page={current_page}"
        )

        try:

            res = requests.get(
                url,
                headers=SPOOFED_HEADERS,
                timeout=30
            )

        except Exception as e:

            st.error(
                f"一覧取得エラー: {e}"
            )

            st.session_state.running = False

            break

        if res.status_code == 404:

            st.success(
                f"🎉 全巡回完了"
            )

            st.session_state.running = False
            st.session_state.current_page = 1

            break

        matches = re.findall(
            r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ',
            res.text
        )

        if not matches:

            st.success(
                f"🎉 全ページ巡回完了"
            )

            st.session_state.running = False
            st.session_state.current_page = 1

            break

        matches = matches[:30]

        new_rows = []

        total_items = len(matches)

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox"
                ]
            )

            context = browser.new_context(
                user_agent=SPOOFED_HEADERS["User-Agent"],
                viewport={
                    "width": 1280,
                    "height": 1000
                },
                locale="ja-JP",
                timezone_id="Asia/Tokyo"
            )

            # webdriver隠し
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            page = context.new_page()

            for idx, match in enumerate(matches):

                if not st.session_state.running:
                    break

                href = match[0]

                full_title = match[1]

                id_match = re.search(
                    r'/(?:products|apparels)/(?:used/)?(\d+)',
                    href
                )

                if not id_match:
                    continue

                card_id = id_match.group(1)

                access_url = (
                    f"https://snkrdunk.com/apparels/{card_id}"
                )

                name, rarity, card_no, pack = parse_title(full_title)

                run_key = f"{name}_{rarity}_{card_no}"

                if run_key in processed_in_this_run:
                    continue

                processed_in_this_run.add(run_key)

                progress_bar.progress(
                    (idx + 1) / total_items
                )

                status_text.write(
                    f"🔄 処理中 ({idx+1}/{total_items}) "
                    f"{name}"
                )

                psa_price, real_product_url = (
                    get_psa10_data_from_page(
                        page,
                        access_url
                    )
                )

                now_str = datetime.now(
                    ZoneInfo("Asia/Tokyo")
                ).strftime("%Y/%m/%d %H:%M:%S")

                row_data = [
                    name,
                    rarity,
                    card_no,
                    pack,
                    psa_price,
                    now_str,
                    real_product_url
                ]

                if run_key in pokemon_map:

                    row_num = pokemon_map[run_key]["row_num"]

                    sheet.update(
                        f"A{row_num}:G{row_num}",
                        [row_data]
                    )

                    if psa_price != "なし":

                        st.toast(
                            f"✏️ 更新: {name} "
                            f"¥{psa_price:,}"
                        )

                    else:

                        st.toast(
                            f"✏️ 更新: {name} "
                            f"価格なし"
                        )

                else:

                    new_rows.append(row_data)

                    current_total_rows += 1

                    pokemon_map[run_key] = {
                        "row_num": current_total_rows
                    }

                    if psa_price != "なし":

                        st.toast(
                            f"➕ 新規: {name} "
                            f"¥{psa_price:,}"
                        )

                    else:

                        st.toast(
                            f"➕ 新規: {name} "
                            f"価格なし"
                        )

                time.sleep(
                    random.uniform(2.5, 4.0)
                )

            browser.close()

        if new_rows and st.session_state.running:

            sheet.append_rows(new_rows)

        if st.session_state.running:

            st.session_state.current_page += 1

            time.sleep(3)

            st.rerun()

        else:

            st.warning(
                f"🛑 停止されました"
            )

            break

    st.session_state.running = False

    st.rerun()
```
