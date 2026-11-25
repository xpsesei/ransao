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
MAX_WORKERS = 8
CHUNK_SIZE = 100
LOOKUP_BUCKETS = 100  # 将 vod_lookup 拆分为 100 个小文件，单文件仅几十 KB

# 复用 HTTP 连接池 + 自动重试
session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
})
retry_strategy = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS * 2, max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)


def atomic_write_json(filepath, data, indent=None):
    """原子写入 JSON，防止中途失败损坏索引"""
    tmp_path = f"{filepath}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        if indent is not None:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        else:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, filepath)


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
    """正确拆分 $$$ 多播放源，优先提取 m3u8 线路"""
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
        # 优先选择包含 .m3u8 且集数最多的线路
        if current_eps:
            has_m3u8 = any(".m3u8" in ep["url"] for ep in current_eps)
            if has_m3u8 and (not best_episodes or not any(".m3u8" in ep["url"] for ep in best_episodes)):
                best_episodes = current_eps
            elif len(current_eps) > len(best_episodes) and not any(".m3u8" in ep["url"] for ep in best_episodes):
                best_episodes = current_eps

    return best_episodes


def safe_int_id(vod):
    try:
        return int(vod.get("id", 0))
    except (ValueError, TypeError):
        return 0


def main():
    os.makedirs("data/lookup", exist_ok=True)
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
                    continue  # 跳过 latest 避免重复读取
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
            continue  # 过滤无可用播放地址的无效数据

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
    active_files = {"categories.json", "search_index.json", "manifest.json"}

    # 先对所有分类完成分片并写入 data_file，再写 cat_latest.json
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
                active_files.add(chunk_file)
            cat_index.append({"name": c_name, "count": len(v_list), "files": sub_files})
        else:
            file_name = f"cat_{safe_hash}.json"
            for vod in v_list:
                vod["data_file"] = file_name
                bucket = safe_int_id(vod) % LOOKUP_BUCKETS
                lookup_buckets[bucket][str(vod["id"])] = file_name
            atomic_write_json(os.path.join("data", file_name), v_list)
            active_files.add(file_name)
            cat_index.append({"name": c_name, "count": len(v_list), "file": file_name})

    # 此时 all_videos 中的每条记录都已拥有正确的 data_file
    latest_items = all_videos[:100]
    atomic_write_json(os.path.join("data", "cat_latest.json"), latest_items)
    active_files.add("cat_latest.json")
    cat_index.insert(0, {"name": "🔥 最新更新", "count": len(latest_items), "file": "cat_latest.json"})

    atomic_write_json("data/categories.json", cat_index, indent=2)

    # 写入分桶索引 data/lookup/00.json ~ 99.json
    for bucket_id, mapping in lookup_buckets.items():
        atomic_write_json(f"data/lookup/{bucket_id:02d}.json", mapping)

    # 生成精简搜索索引（去掉冗余空格进一步瘦身）
    search_index = [
        {
            "id": v.get("id"),
            "title": v.get("title"),
            "category": v.get("category"),
            "pic": v.get("pic"),
            "actor": v.get("actor"),
            "year": v.get("year"),
            "data_file": v.get("data_file"),
        }
        for v in all_videos
    ]
    atomic_write_json("data/search_index.json", search_index)

    # 生成全局数据版本清单，解决前后端缓存错位
    version_str = str(int(time.time()))
    atomic_write_json("data/manifest.json", {"version": version_str, "total": len(all_videos)})

    # 清理过期旧分片
    cleaned_count = 0
    for filename in os.listdir("data"):
        if filename.endswith(".json") and filename not in active_files:
            try:
                os.remove(os.path.join("data", filename))
                cleaned_count += 1
            except Exception:
                pass
    print(f"✅ 清理过期旧分片: {cleaned_count} 个，当前数据版本: {version_str}")

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
