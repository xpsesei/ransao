import json
import os
import time
import urllib.request

BASE_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=detail"
LIST_URL = "http://api.wujinwszy.net/api.php/provide/vod/?ac=list"


def fetch_json(url):
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
  try:
    with urllib.request.urlopen(req, timeout=25) as response:
      return json.loads(response.read().decode("utf-8"))
  except Exception as e:
    print(f"Error fetching {url}: {e}")
    return None


def main():
  os.makedirs("data", exist_ok=True)
  all_json_path = "data/all.json"
  progress_path = "data/progress.json"

  print("1. Fetching official categories from resource site...")
  list_data = fetch_json(LIST_URL)
  official_classes = []
  if list_data and "class" in list_data:
    official_classes = list_data["class"]  # 资源站官方分类列表

  category_map = {str(c["type_id"]): c["type_name"] for c in official_classes}
  category_names = [c["type_name"] for c in official_classes]

  # 判断是否为首次全量采集（如果 all.json 不存在，说明是第一次）
  is_first_run = not os.path.exists(all_json_path)

  existing_videos = {}
  if not is_first_run:
    try:
      with open(all_json_path, "r", encoding="utf-8") as f:
        old_list = json.load(f)
        for v in old_list:
          existing_videos[str(v["id"])] = v
      print(
          f"Database exists ({len(existing_videos)} items). Switching to"
          " INCREMENTAL update mode."
      )
    except Exception:
      is_first_run = True

  collected_videos = []

  if is_first_run:
    print(
        "=== 首次运行：开启全量采集模式（支持断点续传，确保完整采集） ==="
    )
    start_page = 1
    total_pages = 1

    # 检查是否有未完成的断点进度
    if os.path.exists(progress_path):
      try:
        with open(progress_path, "r", encoding="utf-8") as f:
          prog_data = json.load(f)
          start_page = prog_data.get("next_page", 1)
          collected_videos = prog_data.get("videos", [])
          print(
              f"Resuming from breakpoint -> Page {start_page}, already"
              f" collected {len(collected_videos)} videos."
          )
      except Exception:
        pass

    # 获取总页数
    first_res = fetch_json(f"{BASE_URL}&pg=1")
    if not first_res:
      print("Failed to fetch page 1. Aborting.")
      return

    total_pages = int(first_res.get("pagecount", 1))
    print(f"Total pages in resource site: {total_pages}")

    if start_page == 1 and not collected_videos:
      if "list" in first_res:
        collected_videos.extend(first_res["list"])
      start_page = 2

    # 循环抓取所有页面
    for page in range(start_page, total_pages + 1):
      print(f"Downloading full-data page {page}/{total_pages}...")
      res = fetch_json(f"{BASE_URL}&pg={page}")
      if res and "list" in res:
        collected_videos.extend(res["list"])

      # 实时保存断点进度文件（防止中途报错或超时）
      with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(
            {"next_page": page + 1, "videos": collected_videos},
            f,
            ensure_ascii=False,
        )

      time.sleep(0.4)  # 礼貌延迟，防止请求过快

    # 全量采集顺利完成，清除断点文件
    if os.path.exists(progress_path):
      os.remove(progress_path)

    all_data = collected_videos

  else:
    print("=== 日常更新：开启增量采集模式（仅同步最新更新资源） ===")
    # 增量采集：通常采集前 3 页（带 h=24 或直接抓前几页最新的）
    latest_videos = []
    incremental_pages = 3

    for page in range(1, incremental_pages + 1):
      print(f"Fetching latest updates page {page}/{incremental_pages}...")
      res = fetch_json(f"{BASE_URL}&pg={page}&h=24")
      if not res or "list" not in res or not res["list"]:
        res = fetch_json(f"{BASE_URL}&pg={page}")  # 兜底普通分页
      if res and "list" in res:
        latest_videos.extend(res["list"])
      time.sleep(0.4)

    print(f"Fetched {len(latest_videos)} latest items.")

    # 增量合并：将抓到的最新数据更新到现有字典中（相同ID则覆盖最新集数，不同则新增）
    for vod in latest_videos:
      vid = str(vod.get("vod_id"))
      existing_videos[vid] = vod

    all_data = list(existing_videos.values())

  # 统一格式化并按官方分类归档
  processed_videos = []
  categories = {c_name: [] for c_name in category_names}
  categories["未分类"] = []

  for vod in all_data:
    vod_id = vod.get("vod_id")
    title = vod.get("vod_name", "")

    # 分类严格对齐资源站
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

  # 写入总库
  with open(all_json_path, "w", encoding="utf-8") as f:
    json.dump(processed_videos, f, ensure_ascii=False, indent=2)

  # 按官方分类顺序生成各个分类的 JSON 文件和索引
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

  # 处理其他未被官方收录但实际存在的分类
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

  print(f"Sync successfully finished! Total videos: {len(processed_videos)}")


if __name__ == "__main__":
  main()
