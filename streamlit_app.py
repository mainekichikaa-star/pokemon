import streamlit as st
import requests
import re
import time
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from statistics import median
from bs4 import BeautifulSoup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

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
# 停止フラグ
# =========================================

if "stop_flag" not in st.session_state:
    st.session_state.stop_flag = False

# =========================================
# Google Sheets 接続
# =========================================

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

        if "\\n" in pk:
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

        sheet.append_row(HEADERS)

        return sheet

# =========================================
# タイトル解析
# =========================================

def parse_title(full_title):

    pack = ""
    card_no = ""
    rarity = ""
    name = ""

    # パック名
    pack_match = re.search(
        r'\(([^)]+)\)$',
        full_title.strip()
    )

    if pack_match:
        pack = pack_match.group(1)

    # 型番
    bracket_match = re.search(
        r'\[([^\]]+)\]',
        full_title
    )

    if bracket_match:
        card_no = bracket_match.group(1)

    # [ の前
    before_bracket = full_title.split("[")[0].strip()

    rarity_pattern = (
        r'(.*?)\s+('
        + '|'.join(RARITY_LIST)
        + r')$'
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

# =========================================
# PSA10 売買履歴取得
# =========================================

def get_psa10_price(
    session,
    product_url
):

    try:

        # =====================================
        # 商品ページ
        # =====================================

        res = session.get(
            product_url,
            headers=SPOOFED_HEADERS,
            timeout=15
        )

        if res.status_code != 200:
            return "なし"

        soup = BeautifulSoup(
            res.text,
            "html.parser"
        )

        # =====================================
        # 状態ごとの相場URL取得
        # =====================================

        sales_link = soup.select_one(
            'a[href*="sales-histories"]'
        )

        if not sales_link:
            return "なし"

        href = sales_link.get("href")

        if not href:
            return "なし"

        sales_url = (
            "https://snkrdunk.com"
            + href
        )

        # =====================================
        # 売買履歴ページ取得
        # =====================================

        time.sleep(
            random.uniform(1.0, 2.0)
        )

        sales_res = session.get(
            sales_url,
            headers=SPOOFED_HEADERS,
            timeout=15
        )

        if sales_res.status_code != 200:
            return "なし"

        sales_soup = BeautifulSoup(
            sales_res.text,
            "html.parser"
        )

        # =====================================
        # PSA10履歴ブロック取得
        # =====================================

        target_h2 = None

        for h2 in sales_soup.select(
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

        target_ul = target_h2.find_next(
            "ul",
            class_="sales-history"
        )

        if not target_ul:
            return "なし"

        # =====================================
        # 直近6件
        # =====================================

        psa_prices = []

        items = target_ul.select(
            "li.used"
        )

        for item in items:

            price_tag = item.select_one(
                "p.price"
            )

            if not price_tag:
                continue

            price_text = (
                price_tag.get_text(
                    strip=True
                )
            )

            price_num = int(
                re.sub(
                    r"[^\d]",
                    "",
                    price_text
                )
            )

            psa_prices.append(
                price_num
            )

            if len(psa_prices) >= 6:
                break

        if not psa_prices:
            return "なし"

        return int(
            median(psa_prices)
        )

    except Exception as e:

        print(e)

        return "なし"

# =========================================
# Streamlit UI
# =========================================

st.set_page_config(
    page_title="ポケカ価格自動反映",
    layout="wide"
)

st.title(
    "🃏 ポケカ価格自動反映ツール"
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
        "停止リクエストを受け付けました"
    )

# =========================================
# メイン処理
# =========================================

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

    session = requests.Session()

    current_page = 1
    max_pages = 2

    total_count = 0

    while current_page <= max_pages:

        # 停止判定
        if st.session_state.stop_flag:

            st.warning(
                "処理を停止しました"
            )

            st.stop()

        progress_bar.progress(
            current_page / max_pages
        )

        log_area.markdown(
            f"## ページ {current_page}"
        )

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

            res = session.get(
                url,
                headers=SPOOFED_HEADERS,
                timeout=15
            )

        except Exception as e:

            st.error(e)

            break

        if res.status_code != 200:

            st.error(
                f"一覧取得失敗 "
                f"{res.status_code}"
            )

            break

        html = res.text

        # =====================================
        # 商品取得
        # =====================================

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
                "商品なし"
            )

            break

        new_rows = []

        now_str = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime(
            "%Y/%m/%d %H:%M:%S"
        )

        # =====================================
        # 商品ループ
        # =====================================

        for idx, match in enumerate(
            matches
        ):

            # 停止判定
            if st.session_state.stop_flag:

                st.warning(
                    "処理を停止しました"
                )

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

            # =====================================
            # PSA10価格取得
            # =====================================

            psa_price = get_psa10_price(
                session,
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

            # =====================================
            # 更新
            # =====================================

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

        # =====================================
        # 新規追加
        # =====================================

        if new_rows:

            sheet.append_rows(
                new_rows
            )

        current_page += 1

    progress_bar.progress(1.0)

    st.success(
        f"完了 "
        f"{total_count} 件"
    )

    st.balloons()
