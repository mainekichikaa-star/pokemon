import streamlit as st
import requests
import re
import time
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==================================================
# 設定
# ==================================================

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
    "SAR",
    "SR",
    "AR",
    "CHR",
    "CSR",
    "UR",
    "HR",
    "RRR",
    "RR",
    "R",
    "C",
    "U",
    "P",
    "PROMO",
    "MUR",
    "MA"
]

SEARCH_URL = (
    "https://snkrdunk.com/search?"
    "keywords=%E3%83%88%E3%83%AC%E3%82%AB+"
    "%28%E3%82%B7%E3%83%B3%E3%82%B0%E3%83%AB"
    "%E3%82%AB%E3%83%BC%E3%83%89%29"
    "&searchCategoryIds=6%2F33"
    "&brandIds=pokemon"
    "&sort=hottest"
)

# ==================================================
# 停止ボタン用
# ==================================================

if "stop_flag" not in st.session_state:
    st.session_state.stop_flag = False

# ==================================================
# スプレッドシート接続
# ==================================================

def get_sheet():

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    service_account_info = dict(
        st.secrets["gcp_service_account"]
    )

    if "private_key" in service_account_info:

        pk = service_account_info["private_key"]

        pk = pk.replace("\\n", "\n")

        service_account_info["private_key"] = pk

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        service_account_info,
        scope
    )

    client = gspread.authorize(creds)

    workbook = client.open_by_key(
        SPREADSHEET_ID
    )

    try:

        return workbook.worksheet(
            SHEET_NAME
        )

    except gspread.exceptions.WorksheetNotFound:

        sheet = workbook.add_worksheet(
            title=SHEET_NAME,
            rows="1000",
            cols="20"
        )

        sheet.append_row(
            HEADERS
        )

        return sheet

# ==================================================
# 商品名解析
# ==================================================

def parse_title(full_title):

    pack = ""
    card_no = ""
    rarity = ""
    name = ""

    # パック名
    pack_match = re.search(
        r"\(([^)]+)\)$",
        full_title.strip()
    )

    if pack_match:
        pack = pack_match.group(1)

    # 型番
    card_match = re.search(
        r"\[([^\]]+)\]",
        full_title
    )

    if card_match:
        card_no = card_match.group(1)

    before_bracket = full_title.split("[")[0].strip()

    rarity_pattern = (
        r"(.*?)\s+("
        + "|".join(RARITY_LIST)
        + r")$"
    )

    rarity_match = re.search(
        rarity_pattern,
        before_bracket
    )

    if rarity_match:

        name = rarity_match.group(1).strip()
        rarity = rarity_match.group(2).strip()

    else:

        name = before_bracket

    return (
        name,
        rarity,
        card_no,
        pack
    )

# ==================================================
# Playwright HTML取得
# ==================================================

def get_rendered_html(url):

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/124.0.0.0 "
                    "Safari/537.36"
                ),
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                viewport={
                    "width": 1400,
                    "height": 2000
                }
            )

            page = context.new_page()

            page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
            """)

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=90000
            )

            page.wait_for_timeout(7000)

            html = page.content()

            browser.close()

            return html

    except Exception as e:

        return str(e)

# ==================================================
# PSA10価格取得
# ==================================================

def get_psa10_price(product_url):

    sales_url = (
        product_url.rstrip("/")
        + "/sales-histories?slide=right"
    )

    html = get_rendered_html(
        sales_url
    )

    # デバッグ確認用
    # st.write(html[:5000])

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # PSA10見出し
    target_h2 = None

    for h2 in soup.select(
        "h2.size-title"
    ):

        text = h2.get_text(
            strip=True
        )

        if "PSA10" in text:

            target_h2 = h2
            break

    if not target_h2:
        return "なし"

    # その下のul取得
    target_ul = target_h2.find_next(
        "ul",
        class_="sales-history"
    )

    if not target_ul:
        return "なし"

    prices = []

    for li in target_ul.select(
        "li.used"
    ):

        size_tag = li.select_one(
            "p.size"
        )

        if not size_tag:
            continue

        if "PSA10" not in size_tag.get_text():
            continue

        price_tag = li.select_one(
            "p.price"
        )

        if not price_tag:
            continue

        price_text = price_tag.get_text(
            strip=True
        )

        price_num = int(
            re.sub(
                r"[^\d]",
                "",
                price_text
            )
        )

        prices.append(price_num)

        if len(prices) >= 6:
            break

    if not prices:
        return "なし"

    return int(
        median(prices)
    )

# ==================================================
# Streamlit UI
# ==================================================

st.set_page_config(
    page_title="ポケカ価格取得",
    layout="wide"
)

st.title(
    "🃏 ポケカ価格自動反映"
)

st.write(
    "PSA10直近6件中央値を取得"
)

col1, col2 = st.columns(2)

with col1:

    start_button = st.button(
        "🔄 更新開始",
        type="primary"
    )

with col2:

    stop_button = st.button(
        "⛔ 停止"
    )

if stop_button:

    st.session_state.stop_flag = True

    st.warning(
        "停止リクエスト受付"
    )

# ==================================================
# 実行
# ==================================================

if start_button:

    st.session_state.stop_flag = False

    sheet = get_sheet()

    existing_rows = sheet.get_all_values()

    pokemon_map = {}

    if existing_rows:

        for idx, row in enumerate(
            existing_rows[1:],
            start=2
        ):

            while len(row) < 7:
                row.append("")

            key = (
                f"{row[0]}_"
                f"{row[1]}_"
                f"{row[2]}"
            )

            pokemon_map[key] = {
                "row_num": idx
            }

    log_area = st.empty()

    progress_bar = st.progress(0)

    current_page = 1
    max_pages = 5

    total_count = 0

    while current_page <= max_pages:

        if st.session_state.stop_flag:

            st.warning("停止しました")
            st.stop()

        progress_bar.progress(
            current_page / max_pages
        )

        page_url = (
            SEARCH_URL
            + f"&page={current_page}"
        )

        log_area.markdown(
            f"## ページ {current_page}"
        )

        html = get_rendered_html(
            page_url
        )

        product_regex = (
            r'<a[^>]*href="([^"]+?)"'
            r'[^>]*aria-label="([^"]+?) - '
        )

        matches = re.findall(
            product_regex,
            html
        )

        if not matches:

            st.warning(
                f"ページ {current_page} 商品なし"
            )

            break

        new_rows = []

        now_str = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime(
            "%Y/%m/%d %H:%M:%S"
        )

        for idx, match in enumerate(matches):

            if st.session_state.stop_flag:

                st.warning("停止しました")
                st.stop()

            href = match[0]
            full_title = match[1]

            clean_path = href.split("?")[0]

            if "/products/" in clean_path:

                clean_path = clean_path.replace(
                    "/products/",
                    "/apparels/"
                )

            clean_path = clean_path.replace(
                "/used",
                ""
            )

            if not clean_path.startswith(
                "http"
            ):

                product_url = (
                    "https://snkrdunk.com"
                    + clean_path
                )

            else:

                product_url = clean_path

            (
                name,
                rarity,
                card_no,
                pack
            ) = parse_title(
                full_title
            )

            log_area.text(
                f"[{idx+1}/{len(matches)}] "
                f"{name}"
            )

            psa_price = get_psa10_price(
                product_url
            )

            key = (
                f"{name}_"
                f"{rarity}_"
                f"{card_no}"
            )

            row_data = [
                name,
                rarity,
                card_no,
                pack,
                psa_price,
                now_str,
                product_url
            ]

            if key in pokemon_map:

                row_num = pokemon_map[key][
                    "row_num"
                ]

                sheet.update(
                    f"A{row_num}:G{row_num}",
                    [row_data]
                )

            else:

                new_rows.append(
                    row_data
                )

            total_count += 1

            time.sleep(
                random.uniform(
                    1.0,
                    2.5
                )
            )

        if new_rows:

            sheet.append_rows(
                new_rows
            )

        current_page += 1

    progress_bar.progress(1.0)

    st.success(
        f"完了 {total_count} 件"
    )

    st.balloons()
