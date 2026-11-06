import concurrent.futures
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 5
CHUNK_SIZE = 100  # 🎯 将分片大小缩小为 200，保证每个 JSON 文件只有几百 KB，实现秒级下载


def fetch_page(page, retries=3):
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
      with urllib.request.urlopen(req, timeout=40) as response:
        data = json.loads(response.read().decode("utf-8"))
        print(f"-> 成功下载第 {page} 页")
        return data.get("list", [])
    except Exception as e:
      if attempt < retries - 1:
        time.sleep((attempt + 1) * 2)
        continue
      else:
        print(f"❌ 第 {page} 页连续重试失败: {e}")
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

  print("2. 检查总页数并开始全量同步...")
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
  print(f"🔥 采集站总页数: {total_pages}。开始并发抓取...")

  all_videos = []
  if "list" in first_res:
    all_videos.extend(first_res["list"])

  pages_to_fetch = list(range(2, total_pages + 1))

  with concurrent.futures.ThreadPoolExecutor(
      max_workers=MAX_WORKERS
  ) as executor:
    results = executor.map(fetch_page, pages_to_fetch)
    for page_list in results:
      if page_list:
        all_videos.extend(page_list)

  print(f"下载完成！共收集原始影视数据: {len(all_videos)} 条")

  processed_videos = []
  categories = {c_name: [] for c_name in category_names}
  categories["未分类"] = []

  for vod in all_videos:
    vod_id = vod.get("vod_id")
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
            if (
                ".m3u8" in ep_link
                or ".mp4" in ep_link
                or "http" in ep_link
            ):
              episodes.append({"name": ep_name, "url": ep_link})

    item = {
        "id": vod_id,
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
    processed_videos.append(item)

    if category not in categories:
      categories[category] = []
    categories[category].append(item)

  cat_index = []

  # 生成“🔥 最新更新”分类
  latest_items = processed_videos[:100]
  with open(os.path.join("data", "cat_latest.json"), "w", encoding="utf-8") as f:
    json.dump(latest_items, f, ensure_ascii=False, indent=2)
  cat_index.append(
      {"name": "🔥 最新更新", "count": len(latest_items), "file": "cat_latest.json"}
  )

  # 保存常规分类分片文件（每 200 条一个文件）
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

  # 生成全局搜索索引文件
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
  # ⚠️ 请把下方的 https://yourdomain.com 替换为你实际的网站域名或 GitHub Pages 链接
  DOMAIN = "https://198616.xyz"

  sitemap_lines = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
      # 首页权重最高
      f"  <url>",
      f"    <loc>{DOMAIN}/index.html</loc>",
      f"    <changefreq>always</changefreq>",
      f"    <priority>1.0</priority>",
      f"  </url>",
  ]

  # 写入各个分类页链接
  for c_name in category_names:
    encoded_cat = urllib.parse.quote(c_name)
    sitemap_lines.extend([
        "  <url>",
        f"    <loc>{DOMAIN}/index.html?cat={encoded_cat}</loc>",
        f"    <changefreq>hourly</changefreq>",
        f"    <priority>0.8</priority>",
        "  </url>",
    ])

  # 写入所有视频的独立播放页链接
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
  print(f"✅ sitemap.xml 生成成功，共收录 {len(processed_videos)} 个影视页面！")


if __name__ == "__main__":
  main()
