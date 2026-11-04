import json
import os
import urllib.request

# 使用接口的 JSON 模式，效率更高
BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
MAX_PAGES = 5  # 每次采集前 5 页（你可以改成 10 或更多）


def fetch_page(page):
  url = f"{BASE_URL}&pg={page}"
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
  try:
    with urllib.request.urlopen(req, timeout=15) as response:
      return json.loads(response.read().decode("utf-8"))
  except Exception as e:
    print(f"Error fetching page {page}: {e}")
    return None


def main():
  all_videos = []
  print("Starting video synchronization...")

  first_data = fetch_page(1)
  if not first_data or "list" not in first_data:
    print("Failed to fetch initial data.")
    return

  pagecount = int(first_data.get("pagecount", 1))
  total_pages = min(pagecount, MAX_PAGES)
  print(f"Total pages: {pagecount}, fetching up to {total_pages} pages.")

  all_videos.extend(first_data["list"])

  for page in range(2, total_pages + 1):
    print(f"Fetching page {page}/{total_pages}...")
    data = fetch_page(page)
    if data and "list" in data:
      all_videos.extend(data["list"])

  processed_videos = []
  categories = {}

  for vod in all_videos:
    vod_id = vod.get("vod_id")
    title = vod.get("vod_name", "")
    category = vod.get("type_name", "未分类")
    pic = vod.get("vod_pic", "")
    year = vod.get("vod_year", "")
    area = vod.get("vod_area", "")
    actor = vod.get("vod_actor", "")
    director = vod.get("vod_director", "")
    content = vod.get("vod_content", "").strip()
    play_url_raw = vod.get("vod_play_url", "")

    # 解析播放选集
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

  os.makedirs("data", exist_ok=True)

  with open("data/all.json", "w", encoding="utf-8") as f:
    json.dump(processed_videos, f, ensure_ascii=False, indent=2)

  cat_index = []
  for cat, v_list in categories.items():
    safe_name = (
        cat.replace("/", "_").replace("\\", "_").replace(" ", "_").lower()
    )
    file_name = f"{safe_name}.json"
    with open(os.path.join("data", file_name), "w", encoding="utf-8") as f:
      json.dump(v_list, f, ensure_ascii=False, indent=2)
    cat_index.append({"name": cat, "count": len(v_list), "file": file_name})

  with open("data/categories.json", "w", encoding="utf-8") as f:
    json.dump(cat_index, f, ensure_ascii=False, indent=2)

  print(
      f"Sync complete! Total videos: {len(processed_videos)}, Categories:"
      f" {len(cat_index)}"
  )


if __name__ == "__main__":
  main()
