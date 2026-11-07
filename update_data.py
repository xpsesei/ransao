import concurrent.futures
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 3
CHUNK_SIZE = 100


def write_json(path, data, indent=None):
    """以 UTF-8 写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=indent)


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
            print(
                f"⚠️ 第 {page} 页请求异常，"
                f"第 {attempt + 1} 次重试：{error}"
            )
            if attempt < retries - 1:
                time.sleep((attempt + 1) * 3)

    print(f"❌ 第 {page} 页连续重试失败，已跳过")
    return []


def read_existing_videos():
    """读取已生成的分类文件，为增量更新保留旧影片。"""
    category_path = os.path.join("data", "categories.json")
    if not os.path.exists(category_path):
        return {}

    try:
        with open(category_path, "r", encoding="utf-8") as file:
            category_index = json.load(file)

        videos = {}

        for category in category_index:
            files = []

            if category.get("file"):
                files.append(category["file"])

            files.extend(category.get("files", []))

            for file_name in files:
                file_path = os.path.join("data", file_name)
                if not os.path.isfile(file_path):
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        chunk = json.load(file)
                except (OSError, json.JSONDecodeError) as error:
                    print(f"⚠️ 读取旧文件失败 {file_path}：{error}")
                    continue

                if not isinstance(chunk, list):
                    continue

                for video in chunk:
                    video_id = video.get("id")
                    if video_id is not None:
                        videos[str(video_id)] = video

        print(f"成功加载本地现有影视数据：{len(videos)} 条")
        return videos

    except (OSError, json.JSONDecodeError, TypeError) as error:
        print(f"读取本地数据失败，将执行全量采集：{error}")
        return {}


def normalize_video(raw_video, category_map):
    """将采集站记录转换为网站内部使用的数据结构。"""
    type_id = str(raw_video.get("type_id", ""))
    category = (
        raw_video.get("type_name")
        or category_map.get(type_id)
        or "未分类"
    )

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

        if (
            ".m3u8" in episode_url
            or ".mp4" in episode_url
            or "http" in episode_url
        ):
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
        "content": (raw_video.get("vod_content") or "").strip(),
        "episodes": episodes,
    }


def main():
    os.makedirs("data", exist_ok=True)

    print("1. 获取官方分类列表...")
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
    category_names = [
        item["type_name"]
        for item in official_classes
        if item.get("type_name")
    ]

    print("2. 检查采集站状态...")
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

    existing_videos = read_existing_videos()

    if existing_videos:
        fetch_count = min(5, total_pages)
        pages_to_fetch = range(1, fetch_count + 1)
        print(f"⚡ 增量更新：抓取最近 {fetch_count} 页...")
    else:
        pages_to_fetch = range(1, total_pages + 1)
        print(f"🔥 首次全量采集：共 {total_pages} 页...")

    fetched_videos = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        for page_videos in executor.map(fetch_page, pages_to_fetch):
            if page_videos:
                fetched_videos.extend(page_videos)

    print(f"本次抓取数据：{len(fetched_videos)} 条")

    # 新记录覆盖旧记录；新影片追加到数据集。
    for raw_video in fetched_videos:
        video = normalize_video(raw_video, category_map)
        video_id = video.get("id")
        if video_id is not None:
            existing_videos[str(video_id)] = video

    all_videos = list(existing_videos.values())
    print(f"合并后总影视库数据：{len(all_videos)} 条")

    # 按分类整理影片。
    videos_by_category = {name: [] for name in category_names}
    videos_by_category["未分类"] = []

    for video in all_videos:
        category = video.get("category") or "未分类"
        videos_by_category.setdefault(category, []).append(video)

    category_index = []

    # video_index 格式：影片 ID -> 影片所在的分类 JSON 文件。
    video_index = {}

    # 生成最新更新列表。影片索引会在下面指向完整分类数据文件。
    latest_items = all_videos[:100]
    latest_file = "cat_latest.json"
    write_json(
        os.path.join("data", latest_file),
        latest_items,
        indent=2,
    )
    category_index.append({
        "name": "🔥 最新更新",
        "count": len(latest_items),
        "file": latest_file,
    })

    all_categories = list(category_names)
    for category_name in videos_by_category:
        if category_name not in all_categories:
            all_categories.append(category_name)

    for category_name in all_categories:
        video_list = videos_by_category.get(category_name, [])
        if not video_list:
            continue

        safe_hash = hashlib.md5(
            category_name.encode("utf-8")
        ).hexdigest()[:10]

        if len(video_list) > CHUNK_SIZE:
            chunk_files = []

            for start in range(0, len(video_list), CHUNK_SIZE):
                chunk = video_list[start:start + CHUNK_SIZE]
                chunk_number = start // CHUNK_SIZE + 1
                chunk_file = (
                    f"cat_{safe_hash}_part{chunk_number}.json"
                )

                write_json(
                    os.path.join("data", chunk_file),
                    chunk,
                    indent=2,
                )
                chunk_files.append(chunk_file)

                # 将本分片中的影片 ID 映射到分片文件。
                for video in chunk:
                    video_id = video.get("id")
                    if video_id is not None:
                        video_index[str(video_id)] = chunk_file

            category_index.append({
                "name": category_name,
                "count": len(video_list),
                "files": chunk_files,
            })

        else:
            file_name = f"cat_{safe_hash}.json"
            write_json(
                os.path.join("data", file_name),
                video_list,
                indent=2,
            )

            for video in video_list:
                video_id = video.get("id")
                if video_id is not None:
                    video_index[str(video_id)] = file_name

            category_index.append({
                "name": category_name,
                "count": len(video_list),
                "file": file_name,
            })

    write_json(
        os.path.join("data", "categories.json"),
        category_index,
        indent=2,
    )
    write_json(
        os.path.join("data", "video_index.json"),
        video_index,
    )
    print(f"✅ 已生成 video_index.json：{len(video_index)} 条映射")

    # 全局搜索索引只保留列表展示和搜索需要的字段。
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
    write_json(
        os.path.join("data", "search_index.json"),
        search_index,
    )

    # 生成 sitemap.xml。
    print("3. 生成 sitemap.xml...")
    domain = "https://xpsesei.github.io/ransao"

    sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        f"    <loc>{domain}/index.html</loc>",
        "    <changefreq>always</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
    ]

    for category_name in category_names:
        encoded_category = urllib.parse.quote(category_name)
        sitemap_lines.extend([
            "  <url>",
            f"    <loc>{domain}/index.html?cat={encoded_category}</loc>",
            "    <changefreq>hourly</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ])

    for video in all_videos:
        video_id = video.get("id")
        if video_id is None:
            continue

        encoded_category = urllib.parse.quote(
            video.get("category", "未分类")
        )
        sitemap_lines.extend([
            "  <url>",
            f"    <loc>{domain}/play.html?id={video_id}&amp;cat={encoded_category}</loc>",
            "    <changefreq>daily</changefreq>",
            "    <priority>0.6</priority>",
            "  </url>",
        ])

    sitemap_lines.append("</urlset>")

    with open("sitemap.xml", "w", encoding="utf-8") as file:
        file.write("\n".join(sitemap_lines))

    print(f"✅ 更新完成，当前影视总数：{len(all_videos)}")


if __name__ == "__main__":
    main()
