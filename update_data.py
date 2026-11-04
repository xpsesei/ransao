import concurrent.futures
import json
import os
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"
MAX_WORKERS = 10  # 10线程并发，全速采集全量资源


def fetch_page(page):
  url = f"{BASE_URL}&pg={page}"
  req = urllib.request.Request(
      url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
  )
  try:
    with urllib.request.urlopen(req, timeout=30) as response:
      data = json.loads(response.read().decode("utf-8"))
      print(f"-> Successfully downloaded page {page}")
      return data.get("list", [])
  except Exception as e:
    print(f"❌ Error fetching page {page}: {e}")
    return []


def main():
  os.makedirs("data", exist_ok=True)
  all_json_path = "data/all.json"

  print("1. Fetching official categories from resource site...")
  try:
    req = urllib.request.Request(
        LIST_URL, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
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
    with urllib.request.urlopen(req, timeout=20) as resp:
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

  # 写入文件
  with open(all_json_path, "w", encoding="utf-8") as f:
    json.dump(processed_videos, f, ensure_ascii=False, indent=2)

  cat_index = []
  for c_name in category_names:
    v_list = categories.get(c_name, [])
    if v_list:
      safe_name = (
          c_name.replace("/", "_").replace("\\", "_").replace(" ", "_").lower()
      )
      file_name = f"{safe_name}.json"
      with open(os.path.join("data", file_name), "w", encoding="utf-8") as f:
        json.dump(v_list, f, ensure_ascii=False, indent=2)
      cat_index.append({"name": c_name, "count": len(v_list), "file": file_name})

  for c_name, v_list in categories.items():
    if c_name not in category_names and v_list:
      safe_name = (
          c_name.replace("/", "_").replace("\\", "_").replace(" ", "_").lower()
      )
      file_name = f"{safe_name}.json"
      with open(os.path.join("data", file_name), "w", encoding="utf-8") as f:
        json.dump(v_list, f, ensure_ascii=False, indent=2)
      cat_index.append({"name": c_name, "count": len(v_list), "file": file_name})

  with open("data/categories.json", "w", encoding="utf-8") as f:
    json.dump(cat_index, f, ensure_ascii=False, indent=2)

  print(
      f"✅ Full sync processing complete! Total processed videos:"
      f" {len(processed_videos)}"
  )


if __name__ == "__main__":
  main()
