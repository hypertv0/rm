import re
import sys
import os
import json
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
BASE_URL = "https://radimacizle.com/"
KANALLAR_DIR = "kanallar"


def find_working_domain(context):
    print("\n domain aranıyor...")
    page = context.new_page()
    try:
        response = page.goto(BASE_URL, timeout=15000, wait_until="domcontentloaded")
        if response and response.ok:
            final_url = page.url.rstrip("/")
            print(f" domain bulundu: {final_url}")
            return final_url
    except Exception as e:
        print(f" Hata: {e}")
    finally:
        page.close()
    return None


def get_channels(page):
    print(" Kanal verileri çekiliyor...")
    channels = []

    def intercept_response(response):
        if "text/javascript" in response.headers.get("content-type", ""):
            try:
                body = response.text()
                match = re.search(r"const serverData = (\[.*?\]);", body, re.DOTALL)
                if match:
                    data = json.loads(match.group(1))
                    for item in data:
                        if item.get("sport") == "tv":
                            channels.append(item)
            except:
                pass

    page.on("response", intercept_response)
    try:
        page.goto(BASE_URL, timeout=20000, wait_until="networkidle")
        page.wait_for_timeout(3000)
    except:
        pass
    finally:
        page.remove_listener("response", intercept_response)

    if not channels:
        content = page.content()
        match = re.search(r"const serverData = (\[.*?\]);", content, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            for item in data:
                if item.get("sport") == "tv":
                    channels.append(item)

    print(f" {len(channels)} kanal bulundu.")
    return channels


def get_stream_url(context, channel_id, player_type):
    page = context.new_page()
    stream_url = None

    def intercept_request(request):
        nonlocal stream_url
        url = request.url
        if ".m3u8" in url or "index.m3u8" in url:
            stream_url = url

    page.on("request", intercept_request)

    player_base = "https://fr-1.bc4liveiframecdn.shop/player/"
    player_url = f"{player_base}{player_type}.php?id={channel_id}"

    try:
        page.goto(player_url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
    except:
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
                f.write(
                    f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
                )
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
                f.write(
                    f"#EXTVLCOPT:http-user-agent={USER_AGENT}\n"
                )
                f.write(
                    f"#EXTVLCOPT:http-referrer=https://fr-1.bc4liveiframecdn.shop/player/{player_type}.php/\n"
                )
                f.write(f"{stream_url}\n")
                valid_count += 1

    print(f"\n playlist.m3u guncellendi ({valid_count} kanal)")


def main():
    print("=== radyacizle.com IPTV Guncelleyici ===")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)

        domain = find_working_domain(context)
        if not domain:
            print(" calisdomain bulunamadi!")
            sys.exit(1)

        page = context.new_page()
        channels = get_channels(page)
        page.close()

        if not channels:
            print(" Kanal bulunamadi!")
            sys.exit(1)

        print(f"\n Stream linkleri test ediliyor ({len(channels)} kanal)...")
        working_links = {}
        for i, ch in enumerate(channels):
            name = ch.get("home_name", "Bilinmeyen")
            channel_id = ch.get("channel", "")
            player_type = ch.get("player_type", "player2")
            key = f"{channel_id}_{player_type}"

            print(f"  [{i+1}/{len(channels)}] {name}...", end=" ", flush=True)
            stream_url = get_stream_url(context, channel_id, player_type)
            if stream_url:
                working_links[key] = stream_url
                print("OK")
            else:
                working_links[key] = None
                print("BULUNAMADI")

        browser.close()

    create_m3u8_files(channels, working_links)
    create_playlist(channels, working_links)

    print("\n Tamamlandi!")


if __name__ == "__main__":
    main()
