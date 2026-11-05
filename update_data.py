import concurrent.futures
import hashlib
import json
import os
import time
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 5  # 5线程并发，防止触发源站 522 超时


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
        print(f"❌ 第 {page} 页连续重试失败 (522/超时): {e}")
        return []


def main():
  os.makedirs("data", exist_ok=True)

  print("1. Fetching official categories from resource site...")
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

  print("2. Checking total pages from API...")
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
  print(f"🔥 Resource site total pages: {total_pages}. Starting full sync...")

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

  print(f"Download finished! Total raw items collected: {len(all_videos)}")

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
            if ".m3u8" in ep_link or ".mp4" in ep_link or "http" in ep_link:
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

  # ❌ 注意：这里不再保存占用 173MB 的 all.json 文件，彻底避开 Git 100MB 限制！

  cat_index = []
  all_cats = list(category_names)
  for c_name in categories.keys():
    if c_name not in all_cats:
      all_cats.append(c_name)

  for c_name in all_cats:
    v_list = categories.get(c_name, [])
    if v_list:
      safe_hash = hashlib.md5(c_name.encode("utf-8")).hexdigest()[:10]
      file_name = f"cat_{safe_hash}.json"

      with open(os.path.join("data", file_name), "w", encoding="utf-8") as f:
        json.dump(v_list, f, ensure_ascii=False, indent=2)
      cat_index.append({"name": c_name, "count": len(v_list), "file": file_name})

  with open("data/categories.json", "w", encoding="utf-8") as f:
    json.dump(cat_index, f, ensure_ascii=False, indent=2)

  print(
      f"✅ Full sync processing complete! Total processed categories:"
      f" {len(cat_index)}"
  )


if __name__ == "__main__":
  main()
