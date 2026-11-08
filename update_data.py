import concurrent.futures
import hashlib
import html
import json
import os
import time
import urllib.parse
import urllib.request


BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"

# 可在 GitHub Actions 中通过 SITE_URL 覆盖。
SITE_URL = os.environ.get("SITE_URL", "https://198616.xyz").rstrip("/")

MAX_WORKERS = 3
CHUNK_SIZE = 100
LATEST_LIMIT = 100
INCREMENTAL_PAGES = 10

# 每个 sitemap 分片最多放 40,000 个 URL，留出上限余量。
SITEMAP_URL_LIMIT = 40000


def write_json(path, data, indent=None):
    """原子写入 JSON，避免中断时留下不完整文件。"""
    temp_path = f"{path}.tmp"

    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=indent)

    os.replace(temp_path, path)


def fetch_json(url, timeout=60):
    """请求并解析 JSON。遇到 HTTP 或 JSON 错误时抛出异常。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()

    return json.loads(body.decode("utf-8"))


def extract_video_list(payload, page):
    """检查采集接口分页响应格式。"""
    if not isinstance(payload, dict):
        raise ValueError(f"第 {page} 页响应不是 JSON 对象")

    videos = payload.get("list")

    if not isinstance(videos, list):
        raise ValueError(f"第 {page} 页响应缺少有效的 list 数组")

    if not videos:
        raise ValueError(f"第 {page} 页返回空数据")

    return videos


def fetch_page(page, retries=5):
    """抓取一页数据；重试失败后抛错，防止把不完整结果当成功发布。"""
    url = f"{BASE_URL}&pg={page}"

    for attempt in range(retries):
        try:
            payload = fetch_json(url, timeout=60)
            videos = extract_video_list(payload, page)
            print(f"-> 成功下载第 {page} 页，共 {len(videos)} 条")
            return videos

        except Exception as error:
            print(
                f"⚠️ 第 {page} 页请求异常，"
                f"第 {attempt + 1}/{retries} 次尝试：{error}"
            )

            if attempt < retries - 1:
                time.sleep(min((attempt + 1) * 3, 15))

    raise RuntimeError(
        f"第 {page} 页连续 {retries} 次抓取失败；"
        "已停止更新，避免将不完整数据写入网站。"
    )


def read_existing_videos():
    """读取现有分类文件中的影片，供增量更新保留旧数据。"""
    category_path = os.path.join("data", "categories.json")

    if not os.path.exists(category_path):
        return {}

    try:
        with open(category_path, "r", encoding="utf-8") as file:
            category_index = json.load(file)

        if not isinstance(category_index, list):
            raise ValueError("categories.json 的根节点不是数组")

        videos = {}

        for category in category_index:
            if not isinstance(category, dict):
                continue

            files = []

            file_name = category.get("file")
            if isinstance(file_name, str) and file_name:
                files.append(file_name)

            category_files = category.get("files", [])
            if isinstance(category_files, list):
                files.extend(
                    item
                    for item in category_files
                    if isinstance(item, str) and item
                )

            for file_name in files:
                file_path = os.path.join("data", file_name)

                if not os.path.isfile(file_path):
                    print(f"⚠️ 本地分片不存在：{file_path}")
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        chunk = json.load(file)
                except (OSError, json.JSONDecodeError) as error:
                    raise RuntimeError(
                        f"读取旧数据失败：{file_path}：{error}"
                    ) from error

                if not isinstance(chunk, list):
                    raise ValueError(
                        f"旧数据分片格式不是数组：{file_path}"
                    )

                for video in chunk:
                    if not isinstance(video, dict):
                        continue

                    video_id = video.get("id")
                    if video_id is not None:
                        videos[str(video_id)] = video

        print(f"成功加载本地现有影视数据：{len(videos)} 条")
        return videos

    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            f"读取本地影视数据失败，已停止更新：{error}"
        ) from error


def normalize_video(raw_video, category_map):
    """将采集站记录转换为网站内部使用的数据结构。"""
    if not isinstance(raw_video, dict):
        return None

    video_id = raw_video.get("vod_id")
    if video_id is None:
        return None

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

        episode_name = pieces[-2].strip()
        episode_url = pieces[-1].strip()
        lower_url = episode_url.lower()

        if lower_url.startswith(("http://", "https://")):
            episodes.append({
                "name": episode_name,
                "url": episode_url,
            })

    return {
        "id": video_id,
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


def video_index_bucket(video_id):
    """按影片 ID 末两位分桶，返回 00 到 99。"""
    try:
        value = int(str(video_id).strip())
    except (TypeError, ValueError):
        return None

    return f"{value % 100:02d}"


def build_latest_items(videos_by_id, fetched_order):
    """
    优先按本次采集接口的返回顺序生成最新列表；
    本次采集不足 100 条时，再用已有数据补齐。

    排序依赖采集接口的分页顺序；如果接口不按更新时间排序，
    结果就只是近似“最新”。
    """
    latest_items = []
    seen_ids = set()

    for video_id in fetched_order:
        key = str(video_id)
        video = videos_by_id.get(key)

        if video is None or key in seen_ids:
            continue

        latest_items.append(video)
        seen_ids.add(key)

        if len(latest_items) >= LATEST_LIMIT:
            return latest_items

    for video_id, video in videos_by_id.items():
        key = str(video_id)

        if key in seen_ids:
            continue

        latest_items.append(video)
        seen_ids.add(key)

        if len(latest_items) >= LATEST_LIMIT:
            break

    return latest_items


def make_video_url(video_id, category):
    """生成 sitemap 影片页 URL，并转义 XML 特殊字符。"""
    query = urllib.parse.urlencode({
        "id": str(video_id),
        "cat": str(category or "未分类"),
    })

    url = f"{SITE_URL}/play.html?{query}"
    return html.escape(url, quote=True)


def write_sitemaps(category_names, all_videos):
    """生成 sitemap 分片和 sitemap.xml 索引。"""
    print("3. 生成 sitemap 文件...")

    urls = [
        html.escape(f"{SITE_URL}/", quote=True),
        html.escape(f"{SITE_URL}/index.html", quote=True),
    ]

    for category_name in category_names:
        query = urllib.parse.urlencode({"cat": category_name})
        urls.append(
            html.escape(
                f"{SITE_URL}/index.html?{query}",
                quote=True,
            )
        )

    for video in all_videos:
        video_id = video.get("id")
        if video_id is None:
            continue

        urls.append(
            make_video_url(
                video_id,
                video.get("category", "未分类"),
            )
        )

    chunks = [
        urls[start:start + SITEMAP_URL_LIMIT]
        for start in range(0, len(urls), SITEMAP_URL_LIMIT)
    ]

    if not chunks:
        chunks = [[]]

    sitemap_files = []

    for chunk_number, chunk in enumerate(chunks, start=1):
        file_name = f"sitemap-{chunk_number}.xml"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                '<urlset '
                'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            ),
        ]

        for url in chunk:
            lines.extend([
                "  <url>",
                f"    <loc>{url}</loc>",
                "  </url>",
            ])

        lines.append("</urlset>")

        file_path = file_name
        temp_path = f"{file_path}.tmp"

        with open(temp_path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))

        os.replace(temp_path, file_path)
        sitemap_files.append(file_name)
        print(f"-> 写入 {file_name}，共 {len(chunk)} 条 URL")

    index_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            '<sitemapindex '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        ),
    ]

    for file_name in sitemap_files:
        sitemap_url = html.escape(
            f"{SITE_URL}/{file_name}",
            quote=True,
        )
        index_lines.extend([
            "  <sitemap>",
            f"    <loc>{sitemap_url}</loc>",
            "  </sitemap>",
        ])

    index_lines.append("</sitemapindex>")

    temp_path = "sitemap.xml.tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        file.write("\n".join(index_lines))

    os.replace(temp_path, "sitemap.xml")

    active_files = set(sitemap_files)

    for name in os.listdir("."):
        if (
            name.startswith("sitemap-")
            and name.endswith(".xml")
            and name not in active_files
        ):
            try:
                os.remove(name)
            except OSError as error:
                print(f"⚠️ 无法清理旧站点地图 {name}：{error}")


def main():
    os.makedirs("data", exist_ok=True)

    print("1. 获取采集站分类列表...")
    try:
        list_data = fetch_json(LIST_URL, timeout=30)

        if not isinstance(list_data, dict):
            raise ValueError("分类接口响应不是 JSON 对象")

        official_classes = list_data.get("class", [])
("分类接口的 class 字段不是数组")

    except category_map = {
        str(item["type_id"]): item["type_name"]
        for item in official_classes
        if (
            isinstance(item, dict)
            and item.get("type_id") is not None
            and item.get("type_name")
        )
    }

    category_names = list(dict.fromkeys(
        item["type_name"]
        for item in official_classes
        if isinstance(item, dict) and item.get("type_name")
    ))

    print("2. 检查采集站状态...")
    try:
        first_response = fetch_json(
            f"{BASE_URL}&pg=1",
            timeout=_workers=MAX_WORKERS
        ) as executor:
            # executor.map 按页码顺序返回；任一页面失败都会中止更新。
            for page_videos in executor.map(fetch_page, pages_to_fetch):
                fetched_videos.extend(page_videos)

    print(f"本次抓取数据：{len(fetched_videos)} 条")

    fetched_order = []
    seen_fetched_ids = set()

    for raw_video in fetched_videos:
        video = normalize_video(raw_video, category_map)
        if video is None:
            continue

        video_id = str(video["id"])
        existing_videos[video_id] = video

        if video_id not in seen_fetched_ids:
            fetched_order.append(video_id)
            seen_fetched_ids.add(video_id)

    if not existing_videos:
        raise RuntimeError("采集结果为空，已停止更新，避免发布空数据")

    all_videos = list(existing_videos.values())
    print(f"合并后总影视库数据：{len(all_videos)} 条")

    # 分类接口不可用时，从已有影片恢复分类名称。
    if not category_names:
        category_names = list(dict.fromkeys(
            video.get("category") or "未分类"
            for video in all_videos
        ))

    videos_by_category = {name: [] for name in category_names}
    videos_by_category.setdefault("未分类", [])

    for video in all_videos:
        category = video.get("category") or "未分类"
        videos_by_category.setdefault(category, []).append(video)

    latest_items = build_latest_items(existing_videos, fetched_order)

    write_json(
        os.path.join("data", "cat_latest.json"),
        latest_items,
        indent=2,
    )

    category_index = [{
        "name": "🔥 最新更新",
        "count": len(latest_items),
        "file": "cat_latest.json",
    }]

    all_categories = list(category_names)
    for category_name in videos_by_category:
        if category_name not in all_categories:
            all_categories.append(category_name)

    bucket_index = {}

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

                for video in chunk:
                    bucket = video_index_bucket(video.get("id"))
                    if bucket is not None:
                        bucket_index.setdefault(bucket, {})[
                            str(video["id"])
                        ] = chunk_file

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
                bucket = video_index_bucket(video.get("id"))
                if bucket is not None:
                    bucket_index.setdefault(bucket, {})[
                        str(video["id"])
                    ] = file_name

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

    for bucket, mapping in bucket_index.items():
        write_json(
            os.path.join("data", f"video_index_{bucket}.json"),
            mapping,
        )

    # 保留小型总清单；播放页按 ID 直接读取对应 video_index_XX.json。
    small_manifest = {
        "version": 2,
        "bucket_prefix": "video_index_",
        "buckets": [
            f"video_index_{bucket}.json"
            for bucket in sorted(bucket_index)
        ],
    }

    write_json(
        os.path.join("data", "video_index.json"),
        small_manifest,
    )

    indexed_video_count = sum(
        len(mapping) for mapping in bucket_index.values()
    )
    print(
        f"✅ 已生成分桶视频索引：{indexed_video_count} 条映射，"
        f"{len(bucket_index)} 个桶文件"
    )

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

    write_sitemaps(category_names, all_videos)

    print(
        f"✅ 更新完成：影视库 {len(all_videos)} 条，"
        f"最新列表 {len(latest_items)} 条，"
        f"站点地址 {SITE_URL}"
    )


if __name__ == "__main__":
    main()
