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
# Playwright自動インストール
# =========================================

@st.cache_resource
def install_playwright():
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=True,
            capture_output=True
        )
        return True
    except Exception as e:
        st.error(f"Playwright install失敗: {e}")
        return False

with st.spinner("Playwright準備中..."):
    install_playwright()

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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
        "Chrome/136.0.0.0 Safari/537.36"
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
            rows="10000",
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
# 広告・モーダル閉じる
# =========================================

def close_advertisements(page):

    selectors = [

        # Buyee
        ".buyee-bcF-modal-close",
        "#buyee-bcSection .buyee-bcF-modal-close",

        # 共通
        "[class*='close']",
        "[aria-label='Close']",
        "[aria-label='閉じる']",

        # Dialog
        "[role='dialog'] button",

        # SVG閉じる
        "svg",

        # Modal
        ".modal-close",
        ".popup-close",
        ".close-button",

        # 広告
        "[class*='ad'] button",
        "[class*='banner'] button"
    ]

    for selector in selectors:

        try:

            elements = page.locator(selector)

            count = elements.count()

            for i in range(min(count, 10)):

                try:
                    el = elements.nth(i)

                    if el.is_visible(timeout=300):

                        try:
                            el.click(timeout=500)
                            time.sleep(0.2)

                        except:
                            try:
                                page.evaluate(
                                    "(el) => el.click()",
                                    el
                                )
                            except:
                                pass

                except:
                    pass

        except:
            pass

    # ESCキー
    try:
        page.keyboard.press("Escape")
    except:
        pass

# =========================================
# webdriver隠蔽
# =========================================

def apply_anti_detection(context):

    context.add_init_script("""
    
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });

        window.chrome = {
            runtime: {}
        };

        Object.defineProperty(navigator, 'plugins', {
            get: () => [1,2,3,4,5]
        });

        Object.defineProperty(navigator, 'languages', {
            get: () => ['ja-JP', 'ja', 'en-US']
        });

    """)

# =========================================
# PSA10価格取得
# =========================================

def get_psa10_data_from_page(page, product_url):

    target_median = "なし"

    final_url = (
        product_url
        .split("/sales-histories")[0]
        .split("?")[0]
    )

    history_url = f"{final_url}/sales-histories?slide=right"

    try:

        page.goto(
            history_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_load_state("networkidle")

        time.sleep(2)

        # 広告閉じる
        for _ in range(5):
            close_advertisements(page)
            time.sleep(0.5)

        # 人間っぽい動き
        for _ in range(4):

            page.mouse.wheel(
                0,
                random.randint(500, 1200)
            )

            time.sleep(random.uniform(0.8, 1.5))

            close_advertisements(page)

        # PSA10セクション探索
        found = False

        for _ in range(15):

            close_advertisements(page)

            psa_exists = page.evaluate("""

                () => {

                    const all = [...document.querySelectorAll("*")];

                    return all.some(el => {

                        const txt = el.innerText || "";

                        return txt.includes("PSA10");

                    });

                }

            """)

            if psa_exists:
                found = True
                break

            page.mouse.wheel(0, 1200)

            time.sleep(1)

        if not found:
            return "なし", final_url

        # HTML取得
        html_content = page.content()

        soup = BeautifulSoup(html_content, "html.parser")

        text = soup.get_text("\n")

        lines = text.split("\n")

        psa10_prices = []

        # PSA10を含む行から価格取得
        for idx, line in enumerate(lines):

            clean = line.strip()

            if "PSA10" not in clean:
                continue

            # 周辺20行探索
            nearby = lines[idx:idx + 20]

            for nearby_line in nearby:

                nearby_line = nearby_line.strip()

                # ¥12,345
                price_match = re.search(
                    r'¥\s?([\d,]+)',
                    nearby_line
                )

                if not price_match:
                    price_match = re.search(
                        r'([0-9,]{3,})円',
                        nearby_line
                    )

                if not price_match:
                    continue

                try:

                    price = int(
                        price_match.group(1)
                        .replace(",", "")
                    )

                    # 異常値除外
                    if 100 <= price <= 10000000:
                        psa10_prices.append(price)

                except:
                    pass

        # 重複除去
        psa10_prices = list(dict.fromkeys(psa10_prices))

        # 中央値
        if psa10_prices:

            recent_prices = psa10_prices[:6]

            target_median = int(
                median(recent_prices)
            )

    except Exception as e:

        print("PSA10取得エラー:", e)

    return target_median, final_url

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(
    page_title="ポケカPSA10価格監視",
    layout="wide"
)

st.title("🃏 ポケカ PSA10価格監視")

if "running" not in st.session_state:
    st.session_state.running = False

if "current_page" not in st.session_state:
    st.session_state.current_page = 1

col1, col2, col3 = st.columns(3)

with col1:

    if st.button(
        "🔄 最初から開始",
        type="primary",
        disabled=st.session_state.running
    ):

        st.session_state.current_page = 1
        st.session_state.running = True
        st.rerun()

with col2:

    if st.button(
        f"▶️ 続きから再開 ({st.session_state.current_page}ページ目)",
        disabled=st.session_state.running
    ):

        st.session_state.running = True
        st.rerun()

with col3:

    if st.button(
        "🛑 停止",
        disabled=not st.session_state.running
    ):

        st.session_state.running = False
        st.rerun()

progress_bar = st.progress(0)

status_text = st.empty()

log_text = st.empty()

# =========================================
# メイン処理
# =========================================

if st.session_state.running:

    sheet = get_sheet()

    existing_rows = sheet.get_all_values()

    pokemon_map = {}

    for idx, row in enumerate(existing_rows[1:], start=2):

        while len(row) < 7:
            row.append("")

        key = f"{row[0]}_{row[1]}_{row[2]}"

        pokemon_map[key] = idx

    processed_in_this_run = set()

    while st.session_state.running:

        current_page = st.session_state.current_page

        log_text.markdown(
            f"## 📄 Page {current_page}"
        )

        search_url = (
            f"https://snkrdunk.com/search?"
            f"keywords=%E3%83%88%E3%83%AC%E3%82%AB"
            f"&searchCategoryIds=6%2F33"
            f"&brandIds=pokemon"
            f"&page={current_page}"
        )

        try:

            res = requests.get(
                search_url,
                headers=SPOOFED_HEADERS,
                timeout=30
            )

        except Exception as e:

            st.error(f"通信失敗: {e}")

            break

        if res.status_code == 404:

            st.success("全ページ完了")

            st.session_state.running = False

            break

        matches = re.findall(
            r'<a[^>]*href="([^"]+?)"[^>]*aria-label="([^"]+?) - ',
            res.text
        )

        if not matches:

            st.success("巡回完了")

            st.session_state.running = False

            break

        matches = matches[:30]

        updates = []
        append_rows = []

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
                    "width": 1366,
                    "height": 900
                },

                locale="ja-JP",

                timezone_id="Asia/Tokyo"
            )

            apply_anti_detection(context)

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

                key = f"{name}_{rarity}_{card_no}"

                if key in processed_in_this_run:
                    continue

                processed_in_this_run.add(key)

                progress_bar.progress(
                    (idx + 1) / len(matches)
                )

                status_text.write(
                    f"🔄 {name}"
                )

                psa_price, real_url = get_psa10_data_from_page(
                    page,
                    access_url
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
                    real_url
                ]

                if key in pokemon_map:

                    row_num = pokemon_map[key]

                    updates.append({
                        "range": f"A{row_num}:G{row_num}",
                        "values": [row_data]
                    })

                    st.toast(
                        f"✏️ 更新: {name} / {psa_price}"
                    )

                else:

                    append_rows.append(row_data)

                    st.toast(
                        f"➕ 新規: {name} / {psa_price}"
                    )

                time.sleep(
                    random.uniform(2.5, 4.5)
                )

            browser.close()

        # batch update
        if updates:

            body = {
                "valueInputOption": "USER_ENTERED",
                "data": updates
            }

            sheet.batch_update(body)

        # append
        if append_rows:

            latest_rows = sheet.get_all_values()

            existing_keys = set()

            for row in latest_rows[1:]:

                while len(row) < 3:
                    row.append("")

                existing_keys.add(
                    f"{row[0]}_{row[1]}_{row[2]}"
                )

            filtered = []

            for row in append_rows:

                k = f"{row[0]}_{row[1]}_{row[2]}"

                if k not in existing_keys:
                    filtered.append(row)

            if filtered:
                sheet.append_rows(filtered)

        st.session_state.current_page += 1

        time.sleep(3)

        st.rerun()
