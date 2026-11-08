import concurrent.futures
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 3
CHUNK_SIZE = 100


def write_json(path, data, indent=None):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=indent)


def clean_content(value):
    text = str(value or "")
    text = re.sub(r"</?p\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return text.strip()


def fetch_page(page, retries=5):
    url = f"{BASE_URL}&pg={page}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
                print(f"-> 成功下载第 {page} 页")
                return data.get("list", [])
        except Exception as error:
            print(f"⚠️ 第 {page} 页请求异常，第 {attempt + 1} 次重试：{error}")
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)

    print(f"❌ 第 {page} 页连续重试失败，已跳过")
    return []


def normalize_video(raw_video, category_map):
    type_id = str(raw_video.get("type_id", ""))
    category = raw_video.get("type_name") or category_map.get(type_id) or "未分类"

    episodes = []
    raw_play_url = raw_video.get("vod_play_url") or ""

    for part in raw_play_url.split("#"):
        if "$" not in part:
            continue
        pieces = part.split("$")
        if len(pieces) < 2:
            continue

        episode_name = pieces[-2]
        episode_url = pieces[-1]

        if ".m3u8" in episode_url or ".mp4" in episode_url or "http" in episode_url:
            episodes.append({
                "name": episode_name,
                "url": episode_url,
            })

    return {
        "id": raw_video.get("vod_id"),
        "title": raw_video.get("vod_name", ""),
        "category": category,
        "pic": raw_video.get("vod_pic", ""),
        "year": raw_video.get("vod_year", ""),
        "area": raw_video.get("vod_area", ""),
        "actor": raw_video.get("vod_actor", ""),
        "director": raw_video.get("vod_director", ""),
        "content": clean_content(raw_video.get("vod_content")),
        "episodes": episodes,
    }


def summary_from_video(video):
    return {
        "id": video.get("id"),
        "title": video.get("title"),
        "category": video.get("category"),
        "pic": video.get("pic"),
        "year": video.get("year"),
        "area": video.get("area"),
        "actor": video.get("actor"),
        "director": video.get("director"),
    }


def cleanup_unused_data_files(active_files):
    active_set = set(active_files)
    for root, _, files in os.walk("data"):
        for filename in files:
            if not filename.endswith(".json"):
                continue
            full_path = os.path.join(root, filename)
            if filename in active_set:
                continue
            keep_files = {
                "categories.json",
                "search_index.json",
                "video_detail_index.json",
                "sitemap.xml",
            }
            if filename in keep_files:
                continue
            try:
                os.remove(full_path)
                print(f"已清理旧分片：{filename}")
            except OSError:
                pass


def main():
    os.makedirs("data", exist_ok=True)

    print("1. 获取分类列表...")
    try:
        request = urllib.request.Request(
            LIST_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            list_data = json.loads(response.read().decode("utf-8"))
            official_classes = list_data.get("class", [])
    except Exception as error:
        print(f"⚠️ 获取分类列表失败：{error}")
        official_classes = []

    category_map = {
        str(item["type_id"]): item["type_name"]
        for item in official_classes
        if item.get("type_id") is not None and item.get("type_name")
    }

    category_names = list(dict.fromkeys(
        item["type_name"]
        for item in official_classes
        if item.get("type_name")
    ))

    print("2. 拉取所有数据页...")
    try:
        request = urllib.request.Request(
            f"{BASE_URL}&pg=1",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            first_response = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        print(f"无法访问采集接口：{error}")
        return

    try:
        total_pages = max(1, int(first_response.get("pagecount", 1)))
    except (TypeError, ValueError):
        total_pages = 1

    fetched_videos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for page_videos in executor.map(fetch_page, range(1, total_pages + 1)):
            if page_videos:
                fetched_videos.extend(page_videos)

    print(f"本次抓取数据：{len(fetched_videos)} 条")

    all_videos = []
    seen_ids = set()
    for raw_video in fetched_videos:
        video = normalize_video(raw_video, category_map)
        video_id = video.get("id")
        if video_id is None:
            continue
        key = str(video_id)
        if key not in seen_ids:
            seen_ids.add(key)
            all_videos.append(video)

    print(f"去重后总数据：{len(all_videos)} 条")

    videos_by_category = {name: [] for name in category_names}
    videos_by_category["未分类"] = []

    for video in all_videos:
        category = video.get("category") or "未分类"
        videos_by_category.setdefault(category, []).append(video)

    category_index = []
    video_detail_index = {}
    active_files = set()

    for category_name in category_names + ["未分类"]:
        if category_name not in videos_by_category:
            continue
        video_list = videos_by_category.get(category_name, [])
        if not video_list:
            continue

        safe_hash = hashlib.md5(category_name.encode("utf-8")).hexdigest()[:10]

        summary_list = [summary_from_video(video) for video in video_list]
        summary_file = f"cat_{safe_hash}.json"
        write_json(os.path.join("data", summary_file), summary_list, indent=2)
        active_files.add(summary_file)

        detail_file = f"detail_{safe_hash}.json"
        write_json(os.path.join("data", detail_file), video_list, indent=2)
        active_files.add(detail_file)

        for video in video_list:
            video_id = video.get("id")
            if video_id is not None:
                video_detail_index[str(video_id)] = detail_file

        category_index.append({
            "name": category_name,
            "count": len(video_list),
            "file": summary_file,
            "detail_file": detail_file,
        })

    # 处理未在官方分类中出现、但仍存在的分类
    for category_name in list(videos_by_category.keys()):
        if category_name in [item["name"] for item in category_index]:
            continue
        if not videos_by_category.get(category_name):
            continue

        safe_hash = hashlib.md5(category_name.encode("utf-8")).hexdigest()[:10]
        summary_list = [summary_from_video(video) for video in videos_by_category[category_name]]
        summary_file = f"cat_{safe_hash}.json"
        write_json(os.path.join("data", summary_file), summary_list, indent=2)
        active_files.add(summary_file)

        detail_file = f"detail_{safe_hash}.json"
        write_json(os.path.join("data", detail_file), videos_by_category[category_name], indent=2)
        active_files.add(detail_file)

        for video in videos_by_category[category_name]:
            video_id = video.get("id")
            if video_id is not None:
                video_detail_index[str(video_id)] = detail_file

        category_index.append({
            "name": category_name,
            "count": len(videos_by_category[category_name]),
            "file": summary_file,
            "detail_file": detail_file,
        })

    write_json(os.path.join("data", "categories.json"), category_index, indent=2)
    active_files.add("categories.json")

    write_json(os.path.join("data", "video_detail_index.json"), video_detail_index)
    active_files.add("video_detail_index.json")

    search_index = [
        {
            "id": video.get("id"),
            "title": video.get("title"),
            "category": video.get("category"),
            "actor": video.get("actor"),
            "year": video.get("year"),
            "pic": video.get("pic"),
        }
        for video in all_videos
    ]
    write_json(os.path.join("data", "search_index.json"), search_index)
    active_files.add("search_index.json")

    print("3. 生成 sitemap.xml...")
    domain = "https://198616.xyz"

    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        f"    <loc>{domain}/index.html</loc>",
        "    <changefreq>always</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
    ]

    seen_urls = set()
    for category_name in category_names:
        encoded_category = urllib.parse.quote(category_name)
        loc = f"{domain}/index.html?cat={encoded_category}"
        if loc not in seen_urls:
            seen_urls.add(loc)
            sitemap_lines.extend([
                "  <url>",
                f"    <loc>{loc}</loc>",
                "    <changefreq>hourly</changefreq>",
                "    <priority>0.8</priority>",
                "  </url>",
            ])

    for video in all_videos:
        video_id = video.get("id")
        if video_id is None:
            continue
        encoded_category = urllib.parse.quote(video.get("category", "未分类"))
        loc = f"{domain}/play.html?id={video_id}&amp;cat={encoded_category}"
        if loc not in seen_urls:
            seen_urls.add(loc)
            sitemap_lines.extend([
                "  <url>",
                f"    <loc>{loc}</loc>",
                "    <changefreq>daily</changefreq>",
                "    <priority>0.6</priority>",
                "  </url>",
            ])

    sitemap_lines.append("</urlset>")
    with open("sitemap.xml", "w", encoding="utf-8") as file:
        file.write("\n".join(sitemap_lines))

    cleanup_unused_data_files(active_files)

    print(f"✅ 更新完成，当前影视总数：{len(all_videos)}")


if __name__ == "__main__":
    main()
