import concurrent.futures
import hashlib
import json
import os
import time
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 10
CHUNK_SIZE = 100
LOOKUP_BUCKETS = 100       # ID 取模分 100 桶，单文件仅几十 KB
SEARCH_CHUNK_SIZE = 5000   # 搜索索引每 5000 条分片，避免单文件 20MB+ 卡死

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
})
retry_strategy = Retry(
    total=4,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(
    pool_connections=MAX_WORKERS,
    pool_maxsize=MAX_WORKERS * 2,
    max_retries=retry_strategy,
)
session.mount("http://", adapter)
session.mount("https://", adapter)


def atomic_write_json(filepath, data, indent=None):
    """原子写入 JSON，避免运行中断导致索引损坏"""
    tmp_path = f"{filepath}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        if indent is not None:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, filepath)


def safe_int_id(vod):
    try:
        return int(vod.get("id", 0))
    except (ValueError, TypeError):
        return 0


def fetch_page(page):
    url = f"{BASE_URL}&pg={page}"
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        print(f"-> 成功下载第 {page} 页")
        return data.get("list", [])
    except Exception as e:
        print(f"❌ 第 {page} 页请求失败，已跳过: {e}")
        return []


def parse_episodes(vod):
    """按 $$$ 拆分多播放源，优先提取包含 .m3u8 的主线路"""
    play_url_raw = vod.get("vod_play_url", "") or ""
    if not play_url_raw:
        return []

    source_blocks = play_url_raw.split("$$$")
    best_episodes = []

    for block in source_blocks:
        current_eps = []
        for part in block.split("#"):
            if "$" in part:
                sub_parts = part.split("$")
                ep_name = sub_parts[-2].strip() or "正片"
                ep_link = sub_parts[-1].strip()
                if ".m3u8" in ep_link or ".mp4" in ep_link or ep_link.startswith("http"):
                    current_eps.append({"name": ep_name, "url": ep_link})
        if current_eps:
            has_m3u8 = any(".m3u8" in ep["url"] for ep in current_eps)
            best_has_m3u8 = any(".m3u8" in ep["url"] for ep in best_episodes)
            if has_m3u8 and not best_has_m3u8:
                best_episodes = current_eps
            elif has_m3u8 == best_has_m3u8 and len(current_eps) > len(best_episodes):
                best_episodes = current_eps

    return best_episodes


def clean_dir_json(directory, keep_files):
    cleaned = 0
    if not os.path.exists(directory):
        return 0
    for filename in os.listdir(directory):
        if filename.endswith(".json") and filename not in keep_files:
            try:
                os.remove(os.path.join(directory, filename))
                cleaned += 1
            except Exception:
                pass
    return cleaned


def main():
    os.makedirs("data/lookup", exist_ok=True)
    os.makedirs("data/search", exist_ok=True)
    os.makedirs("sitemaps", exist_ok=True)

    print("1. 获取官方分类列表...")
    try:
        list_data = session.get(LIST_URL, timeout=20).json()
        official_classes = list_data.get("class", [])
    except Exception:
        official_classes = []

    category_map = {}
    category_names = []
    for c in official_classes:
        cid = str(c.get("type_id", ""))
        cname = (c.get("type_name") or "").strip()
        if cname:
            category_map[cid] = cname
            if cname not in category_names:
                category_names.append(cname)

    print("2. 检查总页数与本地数据状态...")
    try:
        first_page = session.get(f"{BASE_URL}&pg=1", timeout=20).json()
        total_pages = int(first_page.get("pagecount", 1))
    except Exception as e:
        print(f"获取总页数失败: {e}")
        return

    existing_videos_map = {}
    if os.path.exists("data/categories.json"):
        print("⚡ 检测到本地已有数据库，加载现有数据准备增量更新...")
        try:
            with open("data/categories.json", "r", encoding="utf-8") as f:
                cat_index = json.load(f)
            for cat in cat_index:
                if cat.get("file") == "cat_latest.json":
                    continue
                files = [cat["file"]] if "file" in cat else cat.get("files", [])
                for fname in files:
                    fpath = os.path.join("data", fname)
                    if os.path.exists(fpath):
                        with open(fpath, "r", encoding="utf-8") as cf:
                            for vod in json.load(cf):
                                existing_videos_map[str(vod["id"])] = vod
            print(f"成功加载本地现有影视数据: {len(existing_videos_map)} 条")
        except Exception as e:
            print(f"读取本地缓存出错，将转为全量采集: {e}")
            existing_videos_map = {}

    if not existing_videos_map:
        print(f"🔥 本地无数据，执行【首次全量采集】共 {total_pages} 页...")
        pages_to_fetch = list(range(1, total_pages + 1))
    else:
        fetch_pages_count = min(20, total_pages)
        print(f"⚡ 开始增量拉取最新更新的前 {fetch_pages_count} 页...")
        pages_to_fetch = list(range(1, fetch_pages_count + 1))

    fetched_videos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for page_list in executor.map(fetch_page, pages_to_fetch):
            if page_list:
                fetched_videos.extend(page_list)

    print(f"本次抓取到数据: {len(fetched_videos)} 条")

    for vod in fetched_videos:
        vod_id = vod.get("vod_id")
        if not vod_id:
            continue
        episodes = parse_episodes(vod)
        if not episodes:
            continue

        type_id = str(vod.get("type_id", ""))
        category = vod.get("type_name") or category_map.get(type_id) or "未分类"

        existing_videos_map[str(vod_id)] = {
            "id": vod_id,
            "title": vod.get("vod_name", ""),
            "category": category,
            "pic": vod.get("vod_pic", ""),
            "year": vod.get("vod_year", ""),
            "area": vod.get("vod_area", ""),
            "actor": vod.get("vod_actor", ""),
            "director": vod.get("vod_director", ""),
            "content": (vod.get("vod_content") or "").strip(),
            "episodes": episodes,
        }

    all_videos = list(existing_videos_map.values())
    all_videos.sort(key=safe_int_id, reverse=True)
    print(f"合并并排序后总影视库数据: {len(all_videos)} 条")

    categories = {c_name: [] for c_name in category_names}
    categories["未分类"] = []
    for vod in all_videos:
        cat = vod.get("category", "未分类")
        categories.setdefault(cat, []).append(vod)

    all_cats = list(category_names) + [c for c in categories if c not in category_names]

    cat_index = []
    lookup_buckets = {i: {} for i in range(LOOKUP_BUCKETS)}
    active_data_files = {"categories.json", "manifest.json"}

    # 先完成所有分类分片写入，确保每条影片都带有正确的 data_file
    for c_name in all_cats:
        v_list = categories.get(c_name, [])
        if not v_list:
            continue

        safe_hash = hashlib.md5(c_name.encode("utf-8")).hexdigest()[:10]
        if len(v_list) > CHUNK_SIZE:
            sub_files = []
            for i in range(0, len(v_list), CHUNK_SIZE):
                chunk = v_list[i : i + CHUNK_SIZE]
                chunk_file = f"cat_{safe_hash}_part{i // CHUNK_SIZE + 1}.json"
                for vod in chunk:
                    vod["data_file"] = chunk_file
                    bucket = safe_int_id(vod) % LOOKUP_BUCKETS
                    lookup_buckets[bucket][str(vod["id"])] = chunk_file
                atomic_write_json(os.path.join("data", chunk_file), chunk)
                sub_files.append(chunk_file)
                active_data_files.add(chunk_file)
            cat_index.append({"name": c_name, "count": len(v_list), "files": sub_files})
        else:
            file_name = f"cat_{safe_hash}.json"
            for vod in v_list:
                vod["data_file"] = file_name
                bucket = safe_int_id(vod) % LOOKUP_BUCKETS
                lookup_buckets[bucket][str(vod["id"])] = file_name
            atomic_write_json(os.path.join("data", file_name), v_list)
            active_data_files.add(file_name)
            cat_index.append({"name": c_name, "count": len(v_list), "file": file_name})

    # 此时写入 cat_latest.json，所有记录均已包含 data_file
    latest_items = all_videos[:100]
    atomic_write_json(os.path.join("data", "cat_latest.json"), latest_items)
    active_data_files.add("cat_latest.json")
    cat_index.insert(0, {"name": "🔥 最新更新", "count": len(latest_items), "file": "cat_latest.json"})

    atomic_write_json("data/categories.json", cat_index, indent=2)

    # 写入分桶定位索引 data/lookup/00.json ~ 99.json
    for bucket_id, mapping in lookup_buckets.items():
        atomic_write_json(f"data/lookup/{bucket_id:02d}.json", mapping)

    # 写入分片紧凑数组格式的搜索索引：
    # 每条记录格式：[id, title, category, pic, actor, year, data_file]
    active_search_files = set()
    search_chunk_count = 0
    for i in range(0, len(all_videos), SEARCH_CHUNK_SIZE):
        chunk = all_videos[i : i + SEARCH_CHUNK_SIZE]
        compact_rows = [
            [
                v.get("id"),
                v.get("title") or "",
                v.get("category") or "",
                v.get("pic") or "",
                v.get("actor") or "",
                v.get("year") or "",
                v.get("data_file") or "",
            ]
            for v in chunk
        ]
        s_fname = f"part_{search_chunk_count}.json"
        atomic_write_json(os.path.join("data", "search", s_fname), compact_rows)
        active_search_files.add(s_fname)
        search_chunk_count += 1

    # 生成版本清单
    version_str = str(int(time.time()))
    atomic_write_json(
        "data/manifest.json",
        {
            "version": version_str,
            "total": len(all_videos),
            "search_chunks": search_chunk_count,
        },
    )

    cleaned_data = clean_dir_json("data", active_data_files)
    cleaned_search = clean_dir_json("data/search", active_search_files)
    print(f"✅ 清理旧分片: data={cleaned_data} 个, search={cleaned_search} 个, 当前版本: {version_str}")

    # 生成 Sitemap
    DOMAIN = "https://198616.xyz"
    base_sitemap_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <url><loc>{DOMAIN}/index.html</loc><changefreq>always</changefreq><priority>1.0</priority></url>",
    ]
    for c_name in category_names:
        base_sitemap_lines.append(
            f"  <url><loc>{DOMAIN}/index.html?cat={urllib.parse.quote(c_name)}</loc>"
            f"<changefreq>hourly</changefreq><priority>0.8</priority></url>"
        )
    base_sitemap_lines.append("</urlset>")
    with open("sitemaps/sitemap_base.xml", "w", encoding="utf-8") as f:
        f.write("\n".join(base_sitemap_lines))

    video_sitemap_files = ["sitemap_base.xml"]
    chunk_size_sitemap = 5000
    for i in range(0, len(all_videos), chunk_size_sitemap):
        chunk = all_videos[i : i + chunk_size_sitemap]
        part_num = (i // chunk_size_sitemap) + 1
        v_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for vod in chunk:
            vod_cat = urllib.parse.quote(vod.get("category", "未分类"))
            v_lines.append(
                f"  <url><loc>{DOMAIN}/play.html?id={vod.get('id')}&amp;cat={vod_cat}</loc>"
                f"<changefreq>daily</changefreq><priority>0.6</priority></url>"
            )
        v_lines.append("</urlset>")
        part_filename = f"sitemap_videos_{part_num}.xml"
        with open(os.path.join("sitemaps", part_filename), "w", encoding="utf-8") as f:
            f.write("\n".join(v_lines))
        video_sitemap_files.append(part_filename)

    index_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for sm_file in video_sitemap_files:
        index_lines.extend(["  <sitemap>", f"    <loc>{DOMAIN}/sitemaps/{sm_file}</loc>", "  </sitemap>"])
    index_lines.append("</sitemapindex>")
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))
    print(f"✅ 同步完成，收录影视：{len(all_videos)} 个！")


if __name__ == "__main__":
    main()
