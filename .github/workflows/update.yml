import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 3
CHUNK_SIZE = 100  # 🎯 优化分片大小为 100，让前端加载速度更快


def fetch_page(page, retries=5):
  url = f"{BASE_URL}&pg={page}"
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
          " like Gecko) Chrome/120.0.0.0 Safari/537.36"
      )
  }
  for attempt in range(retries):
    try:
      req = urllib.request.Request(url, headers=headers)
      with urllib.request.urlopen(req, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
        print(f"-> 成功下载第 {page} 页")
        return data.get("list", [])
    except Exception as e:
      print(
          f"⚠️ 第 {page} 页请求异常，正在进行第 {attempt + 1} 次重试..."
      )
      time.sleep((attempt + 1) * 3)
  print(f"❌ 第 {page} 页连续重试失败，已跳过")
  return []


def main():
  os.makedirs("data", exist_ok=True)

  print("1. 获取官方分类列表...")
  try:
    req = urllib.request.Request(
        LIST_URL, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
      list_data = json.loads(resp.read().decode("utf-8"))
      official_classes = list_data.get("class", [])
  except Exception:
    official_classes = []

  category_map = {str(c["type_id"]): c["type_name"] for c in official_classes}
  category_names = [c["type_name"] for c in official_classes]

  # 检查本地是否已有数据，决定是“全量采集”还是“增量更新”
  has_local_data = os.path.exists("data/categories.json")

  print("2. 检查采集站状态...")
  try:
    req = urllib.request.Request(
        f"{BASE_URL}&pg=1", headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
      first_res = json.loads(resp.read().decode("utf-8"))
  except Exception as e:
    print(f"Failed to fetch page 1: {e}")
    return

  total_pages = int(first_res.get("pagecount", 1))

  # 读取本地已有的所有视频数据（用于增量更新时进行合并/更新）
  existing_videos_map = {}
  if has_local_data:
    print(
        "⚡ 检测到本地已有数据库，执行【增量更新模式】（只抓取最近更新的前几页）..."
    )
    try:
      with open("data/categories.json", "r", encoding="utf-8") as f:
        cat_index = json.load(f)
      for cat in cat_index:
        files = []
        if "file" in cat:
          files.append(cat["file"])
        if "files" in cat:
          files.extend(cat["files"])
        for fname in files:
          fpath = os.path.join("data", fname)
          if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
              chunk_data = json.load(f)
              for vod in chunk_data:
                existing_videos_map[str(vod["id"])] = vod
      print(f"成功加载本地现有影视数据: {len(existing_videos_map)} 条")
    except Exception as e:
      print(f"读取本地缓存出错，将转为全量采集: {e}")
      existing_videos_map = {}

  if not existing_videos_map:
    print(f"🔥 本地无数据，执行【首次全量采集】共 {total_pages} 页...")
    pages_to_fetch = list(range(1, total_pages + 1))
  else:
    # 🎯 增量更新：抓取前 5 页（约 100~200 条最新影片），完美覆盖最近 3 天更新
    fetch_pages_count = min(5, total_pages)
    print(f"⚡ 开始增量拉取最新更新的前 {fetch_pages_count} 页...")
    pages_to_fetch = list(range(1, fetch_pages_count + 1))

  fetched_videos = []
  with concurrent.futures.ThreadPoolExecutor(
      max_workers=MAX_WORKERS
  ) as executor:
    results = executor.map(fetch_page, pages_to_fetch)
    for page_list in results:
      if page_list:
        fetched_videos.extend(page_list)

  print(f"本次抓取到数据: {len(fetched_videos)} 条")

  # 处理抓取到的数据并与本地数据合并（更新已存在剧集，插入新剧集）
  for vod in fetched_videos:
    vod_id = str(vod.get("vod_id"))
    title = vod.get("vod_name", "")

    type_id = str(vod.get("type_id", ""))
    category = vod.get("type_name", "")
    if not category and type_id in category_map:
      category = category_map[type_id]
    if not category:
      category = "未分类"

    pic = vod.get("vod_pic", "")
    year = vod.get("vod_year", "")
    area = vod.get("vod_area", "")
    actor = vod.get("vod_actor", "")
    director = vod.get("vod_director", "")
    content = vod.get("vod_content", "").strip()
    play_url_raw = vod.get("vod_play_url", "")

    episodes = []
    if play_url_raw:
      parts = play_url_raw.split("#")
      for p in parts:
        if "$" in p:
          sub_parts = p.split("$")
          if len(sub_parts) >= 2:
            ep_name = sub_parts[-2]
            ep_link = sub_parts[-1]
            if ".m3u8" in ep_link or ".mp4" in ep_link or "http" in ep_link:
              episodes.append({"name": ep_name, "url": ep_link})

    item = {
        "id": vod.get("vod_id"),
        "title": title,
        "category": category,
        "pic": pic,
        "year": year,
        "area": area,
        "actor": actor,
        "director": director,
        "content": content,
        "episodes": episodes,
    }

    # 如果本地已有该剧集则覆盖更新（自动获取最新剧集列表），没有则直接新增
    existing_videos_map[vod_id] = item

  all_videos = list(existing_videos_map.values())
  print(f"合并后总影视库数据: {len(all_videos)} 条")

  processed_videos = all_videos
  categories = {c_name: [] for c_name in category_names}
  categories["未分类"] = []

  for vod in processed_videos:
    cat = vod.get("category", "未分类")
    if cat not in categories:
      categories[cat] = []
    categories[cat].append(vod)

  cat_index = []

  # 生成“🔥 最新更新”分类（取前100条）
  latest_items = processed_videos[:100]
  with open(os.path.join("data", "cat_latest.json"), "w", encoding="utf-8") as f:
    json.dump(latest_items, f, ensure_ascii=False, indent=2)
  cat_index.append(
      {"name": "🔥 最新更新", "count": len(latest_items), "file": "cat_latest.json"}
  )

  all_cats = list(category_names)
  for c_name in categories.keys():
    if c_name not in all_cats:
      all_cats.append(c_name)

  for c_name in all_cats:
    v_list = categories.get(c_name, [])
    if v_list:
      safe_hash = hashlib.md5(c_name.encode("utf-8")).hexdigest()[:10]

      if len(v_list) > CHUNK_SIZE:
        sub_files = []
        for i in range(0, len(v_list), CHUNK_SIZE):
          chunk = v_list[i : i + CHUNK_SIZE]
          chunk_file = f"cat_{safe_hash}_part{i // CHUNK_SIZE + 1}.json"
          with open(os.path.join("data", chunk_file), "w", encoding="utf-8") as f:
            json.dump(chunk, f, ensure_ascii=False, indent=2)
          sub_files.append(chunk_file)
        cat_index.append(
            {"name": c_name, "count": len(v_list), "files": sub_files}
        )
      else:
        file_name = f"cat_{safe_hash}.json"
        with open(os.path.join("data", file_name), "w", encoding="utf-8") as f:
          json.dump(v_list, f, ensure_ascii=False, indent=2)
        cat_index.append(
            {"name": c_name, "count": len(v_list), "file": file_name}
        )

  with open("data/categories.json", "w", encoding="utf-8") as f:
    json.dump(cat_index, f, ensure_ascii=False, indent=2)

  # 生成全局搜索索引
  search_index = []
  for vod in processed_videos:
    search_index.append({
        "id": vod.get("id"),
        "title": vod.get("title"),
        "category": vod.get("category"),
        "actor": vod.get("actor"),
        "year": vod.get("year"),
        "pic": vod.get("pic"),
    })
  with open(
      os.path.join("data", "search_index.json"), "w", encoding="utf-8"
  ) as f:
    json.dump(search_index, f, ensure_ascii=False)

  # 🚀 自动生成用于 SEO 的网站地图 (sitemap.xml)
  print("3. Generating sitemap.xml for SEO...")
  DOMAIN = "https://xpsesei.github.io/ransao"

  sitemap_lines = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
      f"  <url>",
      f"    <loc>{DOMAIN}/index.html</loc>",
      f"    <changefreq>always</changefreq>",
      f"    <priority>1.0</priority>",
      f"  </url>",
  ]

  for c_name in category_names:
    encoded_cat = urllib.parse.quote(c_name)
    sitemap_lines.extend([
        "  <url>",
        f"    <loc>{DOMAIN}/index.html?cat={encoded_cat}</loc>",
        f"    <changefreq>hourly</changefreq>",
        f"    <priority>0.8</priority>",
        "  </url>",
    ])

  for vod in processed_videos:
    vod_id = vod.get("id")
    vod_cat = urllib.parse.quote(vod.get("category", "未分类"))
    sitemap_lines.extend([
        "  <url>",
        f"    <loc>{DOMAIN}/play.html?id={vod_id}&amp;cat={vod_cat}</loc>",
        f"    <changefreq>daily</changefreq>",
        f"    <priority>0.6</priority>",
        "  </url>",
    ])

  sitemap_lines.append("</urlset>")

  with open("sitemap.xml", "w", encoding="utf-8") as f:
    f.write("\n".join(sitemap_lines))
  print(f"✅ 增量同步完成，当前总库收录影视: {len(processed_videos)} 个！")


if __name__ == "__main__":
  main()
