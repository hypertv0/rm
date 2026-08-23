import re
import sys
import os
import json
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
SITE_URL = "https://radimacizle.com/"
KANALLAR_DIR = "kanallar"


def get_iframe_bases(page):
    print("Ana sayfadan player altyapisi cekiliyor...")
    iframe_bases = {}

    def intercept_request(request):
        url = request.url
        if "/player/" in url and ".php?id=" in url:
            match = re.search(r"(/player/\w+\.php)\?id=", url)
            if match:
                base = "https://" + url.split("/")[2] + match.group(1)
                if "onexbet" in url:
                    iframe_bases["onexbet"] = base
                else:
                    iframe_bases["player2"] = base

    page.on("request", intercept_request)
    try:
        page.goto(SITE_URL, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    except:
        pass
    finally:
        page.remove_listener("request", intercept_request)

    if not iframe_bases:
        content = page.content()
        match = re.search(
            r'(https?://[^"\'\s<>]+?/player/\w+\.php)\?id=', content
        )
        if match:
            base = match.group(1)
            iframe_bases["player2"] = base

    if iframe_bases:
        for k, v in iframe_bases.items():
            print(f"  {k}: {v}")
    else:
        print("  Player altyapisi bulunamadi!")

    return iframe_bases


def get_channels(page):
    print("Kanal verileri cekiliyor...")
    channels = []

    content = page.content()
    match = re.search(r"const serverData = (\[.*?\]);", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            for item in data:
                if item.get("sport") == "tv":
                    channels.append(item)
        except json.JSONDecodeError:
            pass

    print(f"  {len(channels)} TV kanali bulundu.")
    return channels


def get_stream_url(context, player_base, channel_id):
    page = context.new_page()
    stream_url = None

    def intercept_request(request):
        nonlocal stream_url
        url = request.url
        if ".m3u8" in url:
            stream_url = url

    page.on("request", intercept_request)

    player_url = f"{player_base}?id={channel_id}"
    try:
        page.goto(player_url, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)

        if not stream_url:
            frames = page.frames
            for frame in frames:
                if frame != page.main_frame:
                    try:
                        frame_url = frame.url
                        if ".m3u8" in frame_url:
                            stream_url = frame_url
                            break
                    except:
                        pass

        if not stream_url:
            content = page.content()
            match = re.search(r'(https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*)', content)
            if match:
                stream_url = match.group(1)

    except Exception as e:
        pass
    finally:
        page.remove_listener("request", intercept_request)
        page.close()

    return stream_url


def clean_name(name):
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")


def create_m3u8_files(channels, working_links):
    os.makedirs(KANALLAR_DIR, exist_ok=True)

    for ch in channels:
        name = ch.get("home_name", "Bilinmeyen")
        channel_id = ch.get("channel", "")
        player_type = ch.get("player_type", "player2")
        key = f"{channel_id}_{player_type}"

        if key in working_links and working_links[key]:
            stream_url = working_links[key]
            safe_name = clean_name(name)
            filename = f"{safe_name}.m3u8"
            filepath = os.path.join(KANALLAR_DIR, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(
                    f'#EXTINF:-1 tvg-id="{safe_name}.tr" tvg-name="{name}",{name}\n'
                )
                f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
                f.write(
                    f"#EXTVLCOPT:http-referrer=https://fr-1.bc4liveiframecdn.shop/player/{player_type}.php/\n"
                )
                f.write(f"{stream_url}\n")

            print(f"  {filename}")


def create_playlist(channels, working_links):
    playlist_path = "playlist.m3u"
    valid_count = 0

    with open(playlist_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for ch in channels:
            name = ch.get("home_name", "Bilinmeyen")
            channel_id = ch.get("channel", "")
            player_type = ch.get("player_type", "player2")
            key = f"{channel_id}_{player_type}"

            if key in working_links and working_links[key]:
                stream_url = working_links[key]
                safe_name = clean_name(name)
                f.write(
                    f'#EXTINF:-1 tvg-id="{safe_name}.tr" tvg-name="{name}",{name}\n'
                )
                f.write(f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n")
                f.write(
                    f"#EXTVLCOPT:http-referrer=https://fr-1.bc4liveiframecdn.shop/player/{player_type}.php/\n"
                )
                f.write(f"{stream_url}\n")
                valid_count += 1

    print(f"\nplaylist.m3u guncellendi ({valid_count} kanal)")


def main():
    print("=== radyacizle.com IPTV Guncelleyici ===\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)

        page = context.new_page()
        iframe_bases = get_iframe_bases(page)
        channels = get_channels(page)
        page.close()

        if not channels:
            print("Kanal bulunamadi!")
            sys.exit(1)

        if not iframe_bases:
            print("Player altyapisi bulunamadi!")
            sys.exit(1)

        player_base = iframe_bases.get("player2") or iframe_bases.get("onexbet")

        print(f"\nStream linkleri test ediliyor ({len(channels)} kanal)...")
        working_links = {}
        for i, ch in enumerate(channels):
            name = ch.get("home_name", "Bilinmeyen")
            channel_id = ch.get("channel", "")
            player_type = ch.get("player_type", "player2")
            key = f"{channel_id}_{player_type}"

            print(f"  [{i+1}/{len(channels)}] {name}...", end=" ", flush=True)

            base = iframe_bases.get(player_type, player_base)
            stream_url = get_stream_url(context, base, channel_id)

            if stream_url:
                working_links[key] = stream_url
                print("OK")
            else:
                working_links[key] = None
                print("BULUNAMADI")

        browser.close()

    print("\nM3U8 dosyalari olusturuluyor...")
    create_m3u8_files(channels, working_links)
    create_playlist(channels, working_links)

    print("\nTamamlandi!")


if __name__ == "__main__":
    main()
