import streamlit as st
import pandas as pd
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

# =========================
# 設定
# =========================

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
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
}

# =========================
# Google Sheets 接続
# =========================

def get_sheet():

    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    if "gcp_service_account" not in st.secrets:
        st.error("Secretsに gcp_service_account がありません")
        st.stop()

    try:
        service_account_info = dict(
            st.secrets["gcp_service_account"]
        )

        if "private_key" in service_account_info:

            pk = service_account_info["private_key"]

            lines = [
                line.strip()
                for line in pk.split("\n")
                if line.strip()
            ]

            if (
                any("BEGIN PRIVATE KEY" in l for l in lines)
                and
                any("END PRIVATE KEY" in l for l in lines)
            ):

                clean_lines = [
                    l for l in lines
                    if "PRIVATE KEY" not in l
                ]

                inner_key = "".join(clean_lines)

                service_account_info["private_key"] = (
                    "-----BEGIN PRIVATE KEY-----\n"
                    f"{inner_key}\n"
                    "-----END PRIVATE KEY-----\n"
                )

            else:
                service_account_info["private_key"] = (
                    pk.replace("\\n", "\n")
                )

    except Exception as e:
        st.error(f"Secrets解析エラー: {e}")
        st.stop()

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

# =========================
# タイトル解析
# =========================

def parse_title(full_title):

    pack = ""
    card_no = ""
    rarity = ""
    name = ""

    # 収録パック
    pack_match = re.search(r'\(([^)]+)\)$', full_title.strip())

    if pack_match:
        pack = pack_match.group(1)

    # 型番
    bracket_match = re.search(r'\[([^\]]+)\]', full_title)

    if bracket_match:
        card_no = bracket_match.group(1)

    # [ より前
    before_bracket = full_title.split("[")[0].strip()

    rarity_pattern = (
        r'(.*?)\s+('
        + '|'.join(RARITY_LIST)
        + r')$'
    )

    rarity_match = re.search(rarity_pattern, before_bracket)

    if rarity_match:
        name = rarity_match.group(1).strip()
        rarity = rarity_match.group(2).strip()
    else:
        name = before_bracket

    return name, rarity, card_no, pack

# =========================
# PSA10価格取得
# =========================

def get_psa10_price(session, product_url):

    psa10_prices = []

    try:

        res = session.get(
            f"{product_url}/used",
            headers=SPOOFED_HEADERS,
            timeout=15
        )

        time.sleep(random.uniform(1.5, 3.5))

        if res.status_code != 200:
            return "なし"

        soup = BeautifulSoup(res.text, "html.parser")

        history_items = soup.select(
            "ul.sales-history li.used"
        )

        for item in history_items:

            size_tag = item.select_one("p.size")
            price_tag = item.select_one("p.price")

            if not size_tag or not price_tag:
                continue

            size_text = size_tag.get_text(strip=True)

            # PSA10のみ
            if size_text != "PSA10":
                continue

            price_text = price_tag.get_text(strip=True)

            price_num = int(
                re.sub(r"[^\d]", "", price_text)
            )

            psa10_prices.append(price_num)

            # 直近6件
            if len(psa10_prices) >= 6:
                break

        # PSA10履歴なし
        if not psa10_prices:
            return "なし"

        # 中央値
        return int(median(psa10_prices))

    except Exception as e:
        print(e)
        return "なし"

# =========================
# Streamlit UI
# =========================

st.set_page_config(
    page_title="ポケカ価格自動反映ツール",
    layout="wide"
)

st.title("🃏 ポケカ価格自動反映ツール")

st.write(
    "スニダンのPSA10直近6件中央値を"
    "Googleスプレッドシートへ反映します。"
)

st.sidebar.header("設定")

st.sidebar.info(
    f"シート名: {SHEET_NAME}\n\n"
    f"Spreadsheet ID:\n{SPREADSHEET_ID}"
)

# =========================
# 実行
# =========================

if st.button("🔄 価格更新開始", type="primary"):

    try:

        sheet = get_sheet()

        st.success(
            "Googleスプレッドシート接続成功"
        )

    except Exception as e:

        st.error(f"接続失敗: {e}")

        st.stop()

    # =========================
    # 既存データ取得
    # =========================

    existing_rows = sheet.get_all_values()

    pokemon_map = {}

    if existing_rows:

        for idx, row in enumerate(
            existing_rows[1:],
            start=2
        ):

            while len(row) < 7:
                row.append("")

            key = f"{row[0]}_{row[1]}_{row[2]}"

            pokemon_map[key] = {
                "row_num": idx,
                "price": row[4]
            }

    # =========================
    # 表示UI
    # =========================

    log_area = st.empty()

    progress_bar = st.progress(0)

    # =========================
    # requests session
    # =========================

    session = requests.Session()

    # =========================
    # ページ巡回
    # =========================

    current_page = 1
    max_pages = 2

    total_count = 0

    while current_page <= max_pages:

        progress_bar.progress(
            current_page / max_pages
        )

        log_area.markdown(
            f"## 📄 ページ {current_page} 解析中..."
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

            time.sleep(random.uniform(1.5, 3.5))

        except Exception as e:

            st.warning(
                f"通信失敗 page={current_page} : {e}"
            )

            current_page += 1

            continue

        if res.status_code != 200:

            st.error(
                f"スニダン接続失敗: {res.status_code}"
            )

            break

        html = res.text

        # =========================
        # 商品取得
        # =========================

        product_regex = (
            r'<a[^>]*href="([^"]+?)"'
            r'[^>]*aria-label="([^"]+?) - ¥([\d,]+)"'
        )

        matches = re.findall(product_regex, html)

        if not matches:

            st.warning(
                f"ページ {current_page} 商品なし"
            )

            break

        log_area.text(
            f"{len(matches)} 件の商品検知"
        )

        new_rows = []

        now_str = datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).strftime("%Y/%m/%d %H:%M:%S")

        # =========================
        # 商品ループ
        # =========================

        for idx, match in enumerate(matches):

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

            if not clean_path.startswith("http"):

                clean_product_url = (
                    "https://snkrdunk.com"
                    + clean_path
                )

            else:
                clean_product_url = clean_path

            # タイトル解析
            (
                name,
                rarity,
                card_no,
                pack
            ) = parse_title(full_title)

            log_area.text(
                f"[{idx+1}/{len(matches)}] "
                f"{name} [{card_no}]"
            )

            # PSA10価格取得
            psa_price = get_psa10_price(
                session,
                clean_product_url
            )

            key = (
                f"{name}_{rarity}_{card_no}"
            )

            # =========================
            # 更新
            # =========================

            if key in pokemon_map:

                row_num = pokemon_map[key]["row_num"]

                sheet.update(
                    f"E{row_num}:G{row_num}",
                    [[
                        psa_price,
                        now_str,
                        clean_product_url
                    ]]
                )

            else:

                new_rows.append([
                    name,
                    rarity,
                    card_no,
                    pack,
                    psa_price,
                    now_str,
                    clean_product_url
                ])

            total_count += 1

        # =========================
        # 新規追加
        # =========================

        if new_rows:

            sheet.append_rows(new_rows)

        current_page += 1

    progress_bar.progress(1.0)

    st.balloons()

    st.success(
        f"完了: {total_count} 件更新"
    )
