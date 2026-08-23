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
STREAM_WAIT_MS = 5000
CHANNEL_WAIT_MS = 3000


def log(msg, end="\n"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", end=end, flush=True)


def fetch_server_data():
    log("Sunucu verisi cekiliyor...")
    req = Request(SITE_URL, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    match = re.search(r"const serverData = (\[.*?\]);", html, re.DOTALL)
    if not match:
        log("HATA: serverData bulunamadi!")
        sys.exit(1)
    data = json.loads(match.group(1))

    tv = []
    matches = []
    for item in data:
        if item.get("sport") == "tv":
            tv.append(item)
        else:
            matches.append(item)

    tv.sort(key=lambda x: x.get("home_name", ""))
    matches.sort(key=lambda x: (
        x.get("league", ""),
        x.get("time", "99:99"),
        x.get("home_name", ""),
    ))

    log(f"  {len(tv)} TV kanali, {len(matches)} mac bulundu.")
    return tv, matches


def get_unique_channel_ids(tv_channels, matches):
    all_items = tv_channels + matches
    seen = {}
    for item in all_items:
        cid = item.get("channel", "")
        ptype = item.get("player_type", "player2")
        key = f"{cid}_{ptype}"
        if key not in seen:
            seen[key] = item
    return list(seen.values())


def extract_streams_batch(context, unique_items):
    log(f"Stream taramasi basliyor ({len(unique_items)} benzersiz kanal)...")
    page = context.new_page()
    all_streams = {}

    def on_request(request):
        url = request.url
        if ".m3u8" in url and "index.m3u8" in url and url not in all_streams:
            all_streams[url] = True
            cid_match = re.search(r"/(\w+)/index\.m3u8", url)
            cid = cid_match.group(1) if cid_match else "?"
            log(f"  + [{cid}] yakalandi")

    page.on("request", on_request)

    try:
        page.goto(SITE_URL, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(CHANNEL_WAIT_MS)
    except Exception as e:
        log(f"HATA: Sayfa yuklenemedi: {e}")
        page.close()
        return {}

    total = len(unique_items)
    for i, item in enumerate(unique_items):
        cid = item.get("channel", "")
        ptype = item.get("player_type", "player2")
        sport = item.get("sport", "tv")
        name = item.get("home_name", "")

        if sport == "tv":
            title = name
            status = "live"
        else:
            away = item.get("away_name", "")
            title = f"{name} - {away}"
            status = _get_match_status(item.get("time", ""))

        if not cid:
            continue

        log(f"  [{i+1}/{total}] {name} ({cid})...", end=" ")
        try:
            page.evaluate(
                """(args) => {
                const [title, status, channelId, playerType] = args;
                if (typeof loadMatch === 'function') {
                    loadMatch(title, status, channelId, null, playerType);
                }
            }""",
                [title, status, cid, ptype],
            )
            page.wait_for_timeout(STREAM_WAIT_MS)
            log(f"OK ({len(all_streams)} stream)")
        except Exception as e:
            log(f"HATA: {e}")

    page.close()
    return all_streams


def _get_match_status(time_str):
    if not time_str:
        return "upcoming"
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return "upcoming"
        now = time.localtime()
        match_minutes = int(parts[0]) * 60 + int(parts[1])
        now_minutes = now.tm_hour * 60 + now.tm_min
        diff = match_minutes - now_minutes
        if diff < -120:
            return "finished"
        elif diff < 0:
            return "live"
        elif diff <= 30:
            return "soon"
        else:
            return "upcoming"
    except:
        return "upcoming"


def match_streams(all_items, all_streams):
    log("Stream linklerini kanallara eslestiriyor...")
    result = {}

    for item in all_items:
        cid = item.get("channel", "")
        ptype = item.get("player_type", "player2")
        key = f"{cid}_{ptype}"

        if not cid or key in result:
            continue

        matched = None
        for url in all_streams:
            path_match = re.search(r"/(\w+)/index\.m3u8", url)
            if path_match and path_match.group(1) == cid:
                matched = url
                break

        if not matched:
            for url in all_streams:
                if f"/{cid}/" in url:
                    matched = url
                    break

        result[key] = matched

    found = sum(1 for v in result.values() if v)
    log(f"  {found}/{len(result)} eslesme bulundu.")
    return result


def clean_name(name):
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")


def _get_group(sport, item):
    if sport == "tv":
        return "TV Kanallari"
    league = item.get("league", "Diger")
    sport_type = item.get("sport", "")
    sport_names = {
        "futbol": "Futbol",
        "voleybol": "Voleybol",
        "basketbol": "Basketbol",
        "e_sporlar": "E-Spor",
        "kriket": "Kriket",
        "buz_hokeyi": "Buz Hokeyi",
        "tenis": "Tenis",
    }
    return f"{sport_names.get(sport_type, sport_type)} - {league}"


def create_m3u8_files(tv_channels, matches, working_links):
    os.makedirs(KANALLAR_DIR, exist_ok=True)
    count = 0
    all_items = tv_channels + matches

    for item in all_items:
        name = item.get("home_name", "")
        channel_id = item.get("channel", "")
        player_type = item.get("player_type", "player2")
        sport = item.get("sport", "tv")

        if sport != "tv":
            away = item.get("away_name", "")
            display_name = f"{name} vs {away}"
        else:
            display_name = name

        key = f"{channel_id}_{player_type}"
        if key not in working_links or not working_links[key]:
            continue

        stream_url = working_links[key]
        safe_name = clean_name(display_name)
        filepath = os.path.join(KANALLAR_DIR, f"{safe_name}.m3u8")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            f.write(
                f'#EXTINF:-1 tvg-id="{safe_name}.tr" '
                f'tvg-name="{display_name}" '
                f'group-title="{_get_group(sport, item)}",'
                f"{display_name}\n"
            )
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(
                f"#EXTVLCOPT:http-referrer="
                f"{PLAYER_CDN}/player/{player_type}.php/\n"
            )
            f.write(f"{stream_url}\n")
        count += 1

    log(f"  {count} m3u8 dosyasi olusturuldu.")
    return count


def create_playlist(tv_channels, matches, working_links):
    all_items = tv_channels + matches
    valid_count = 0

    sorted_items = sorted(all_items, key=lambda x: (
        0 if x.get("sport") == "tv" else 1,
        _get_group(x.get("sport", ""), x),
        x.get("home_name", ""),
    ))

    with open("playlist.m3u", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")

        for item in sorted_items:
            name = item.get("home_name", "")
            channel_id = item.get("channel", "")
            player_type = item.get("player_type", "player2")
            sport = item.get("sport", "tv")

            if sport != "tv":
                away = item.get("away_name", "")
                display_name = f"{name} vs {away}"
            else:
                display_name = name

            key = f"{channel_id}_{player_type}"
            if key not in working_links or not working_links[key]:
                continue

            stream_url = working_links[key]
            safe_name = clean_name(display_name)
            f.write(
                f'#EXTINF:-1 tvg-id="{safe_name}.tr" '
                f'tvg-name="{display_name}" '
                f'group-title="{_get_group(sport, item)}",'
                f"{display_name}\n"
            )
            f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
            f.write(
                f"#EXTVLCOPT:http-referrer="
                f"{PLAYER_CDN}/player/{player_type}.php/\n"
            )
            f.write(f"{stream_url}\n")
            valid_count += 1

    log(f"  playlist.m3u guncellendi ({valid_count} kanal)")
    return valid_count


def main():
    log("=" * 50)
    log("radyacizle.com IPTV Otomatik Guncelleyici")
    log("=" * 50)

    tv_channels, matches = fetch_server_data()
    unique_items = get_unique_channel_ids(tv_channels, matches)
    log(f"  {len(unique_items)} benzersiz kanal ID taranacak.")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
        )

        all_streams = extract_streams_batch(context, unique_items)
        browser.close()

    if not all_streams:
        log("Hicbir stream linki bulunamadi! Mevcut dosyalar korunuyor.")
        sys.exit(0)

    log(f"\nToplam {len(all_streams)} benzersiz stream linki yakalandi.")

    working_links = match_streams(tv_channels + matches, all_streams)

    log("\nDosyalar olusturuluyor...")
    create_m3u8_files(tv_channels, matches, working_links)
    create_playlist(tv_channels, matches, working_links)

    log("\n" + "=" * 50)
    log("Tamamlandi!")
    log("=" * 50)


if __name__ == "__main__":
    main()
