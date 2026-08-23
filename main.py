import re
import sys
import os
import json
import time
from urllib.request import urlopen, Request
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
SITE_URL = "https://radimacizle.com/"
PLAYER_CDN = "https://fr-1.bc4liveiframecdn.shop"
KANALLAR_DIR = "kanallar"


def fetch_server_data():
    req = Request(SITE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    match = re.search(r"const serverData = (\[.*?\]);", html, re.DOTALL)
    if not match:
        print("serverData bulunamadi!")
        sys.exit(1)
    data = json.loads(match.group(1))
    tv = [d for d in data if d.get("sport") == "tv"]
    matches = [d for d in data if d.get("sport") != "tv"]
    return tv, matches


def extract_streams_from_page(context):
    print("Ana sayfa uzerinden stream linkleri cekiliyor...")
    page = context.new_page()
    all_streams = {}

    def on_request(request):
        url = request.url
        if ".m3u8" in url and url not in all_streams:
            all_streams[url] = True
            print(f"    yakalandi: {url[:80]}...")

    page.on("request", on_request)

    try:
        page.goto(SITE_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
    except Exception as e:
        print(f"  Sayfa yuklenemedi: {e}")
        page.close()
        return {}

    tv_channels, matches = fetch_server_data()
    all_items = tv_channels + matches
    total = len(all_items)

    print(f"  {total} kanal/mac tetiklenecek...")

    for i, item in enumerate(all_items):
        name = item.get("home_name", item.get("away_name", "?"))
        if item.get("sport") == "tv":
            channel_id = item.get("channel", "")
            player_type = item.get("player_type", "player2")
            status = "live"
            title = name
        else:
            channel_id = item.get("channel", "")
            player_type = item.get("player_type", "player2")
            t = item.get("time", "")
            status_class = "soon"
            if t:
                parts = t.split(":")
                if len(parts) == 2:
                    now = time.localtime()
                    match_hour = int(parts[0])
                    match_min = int(parts[1])
                    now_minutes = now.tm_hour * 60 + now.tm_min
                    match_minutes = match_hour * 60 + match_min
                    diff = match_minutes - now_minutes
                    if diff < -120:
                        status_class = "finished"
                    elif diff < 0:
                        status_class = "live"
                    elif diff <= 30:
                        status_class = "soon"
                    else:
                        status_class = "upcoming"
            status = status_class
            title = f"{item.get('home_name', '')} - {item.get('away_name', '')}"

        if not channel_id:
            continue

        print(f"  [{i+1}/{total}] {name} (id={channel_id})...", end=" ", flush=True)

        try:
            page.evaluate(
                """(args) => {
                const [title, status, channelId, playerType] = args;
                loadMatch(title, status, channelId, null, playerType);
            }""",
                [title, status, channel_id, player_type],
            )
            page.wait_for_timeout(8000)
        except Exception as e:
            print(f"HATA: {e}")
            continue

        print(f"({len(all_streams)} stream)")

    page.close()
    return all_streams


def match_streams_to_items(items, all_streams):
    result = {}
    for item in items:
        name = item.get("home_name", "")
        channel_id = item.get("channel", "")
        player_type = item.get("player_type", "player2")
        sport = item.get("sport", "")
        key = f"{channel_id}_{player_type}"

        if not channel_id:
            continue

        matched = None
        for url in all_streams:
            if f"/{channel_id}/" in url:
                matched = url
                break

        if not matched:
            for url in all_streams:
                if channel_id in url:
                    matched = url
                    break

        if not matched and sport == "tv":
            for url in all_streams:
                if f"id={channel_id}" in url:
                    matched = url
                    break

        result[key] = matched

    return result


def clean_name(name):
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")


def create_m3u8_files(channels, working_links):
    os.makedirs(KANALLAR_DIR, exist_ok=True)
    count = 0
    for ch in channels:
        name = ch.get("home_name", "Bilinmeyen")
        channel_id = ch.get("channel", "")
        player_type = ch.get("player_type", "player2")
        key = f"{channel_id}_{player_type}"
        if key in working_links and working_links[key]:
            stream_url = working_links[key]
            safe_name = clean_name(name)
            filepath = os.path.join(KANALLAR_DIR, f"{safe_name}.m3u8")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(f'#EXTINF:-1 tvg-id="{safe_name}.tr" tvg-name="{name}",{name}\n')
                f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
                f.write(f"#EXTVLCOPT:http-referrer={PLAYER_CDN}/player/{player_type}.php/\n")
                f.write(f"{stream_url}\n")
            count += 1
    return count


def create_playlist(channels, working_links):
    valid_count = 0
    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            name = ch.get("home_name", "Bilinmeyen")
            channel_id = ch.get("channel", "")
            player_type = ch.get("player_type", "player2")
            key = f"{channel_id}_{player_type}"
            if key in working_links and working_links[key]:
                stream_url = working_links[key]
                safe_name = clean_name(name)
                f.write(f'#EXTINF:-1 tvg-id="{safe_name}.tr" tvg-name="{name}",{name}\n')
                f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
                f.write(f"#EXTVLCOPT:http-referrer={PLAYER_CDN}/player/{player_type}.php/\n")
                f.write(f"{stream_url}\n")
                valid_count += 1
    return valid_count


def main():
    print("=== radyacizle.com IPTV Guncelleyici ===\n")

    tv_channels, matches = fetch_server_data()
    print(f"  {len(tv_channels)} TV kanali, {len(matches)} mac bulundu.\n")

    all_items = tv_channels + matches

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )

        all_streams = extract_streams_from_page(context)
        browser.close()

    if not all_streams:
        print("\nHicbir stream linki bulunamadi!")
        sys.exit(0)

    print(f"\nToplam {len(all_streams)} stream linki yakalandi.")
    print("\nStream linklerini kanallara eslestiriliyor...")

    working_links = match_streams_to_items(all_items, all_streams)

    found = sum(1 for v in working_links.values() if v)
    print(f"  {found}/{len(all_items)} eslesme bulundu.")

    print("\nM3U8 dosyalari olusturuluyor...")
    m3u8_count = create_m3u8_files(tv_channels, working_links)
    playlist_count = create_playlist(tv_channels, working_links)
    print(f"  {m3u8_count} kanal .m3u8 dosyasi, {playlist_count} playlist kanali")

    print("\nTamamlandi!")


if __name__ == "__main__":
    main()
