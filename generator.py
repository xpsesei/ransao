import os
import json
import random
import time
import subprocess
from openai import OpenAI

CONFIG_PATH = "config.json"
REGISTRY_PATH = "registry.json"
LOG_PATH = "execution.log"

def log_message(msg):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{os.popen('date').read().strip()}] {msg}\n")
    print(msg)

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except:
                return default
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

MASTER_TOOL_POOL = [
    {"name": "JSON to YAML & YAML to JSON Converter", "slug": "json-yaml-converter"},
    {"name": "Cron Expression Schedule Generator & Explainer", "slug": "cron-generator"},
    {"name": "Base64 Image and Text Encoder/Decoder", "slug": "base64-tool"},
    {"name": "JWT (JSON Web Token) Decoder and Validator", "slug": "jwt-decoder"},
    {"name": "Regex Regular Expression Tester & Cheat Sheet", "slug": "regex-tester"},
    {"name": "UUID v4 & Hash (MD5/SHA256) Generator", "slug": "hash-uuid-generator"},
    {"name": "SQL Query Formatter and Minifier", "slug": "sql-formatter"},
    {"name": "HTML/Markdown Live Preview Editor", "slug": "markdown-editor"},
    {"name": "Subnet Mask & CIDR IP Calculator", "slug": "subnet-calculator"},
    {"name": "Unix Timestamp Converter", "slug": "timestamp-converter"},
    {"name": "CSS Box Shadow & Gradient Generator", "slug": "css-generator"},
    {"name": "URL Safe Encoder and Query String Parser", "slug": "url-parser"},
    {"name": "Markdown Table Generator & Formatter", "slug": "markdown-table-generator"},
    {"name": "RSA Key Pair & Certificate Generator", "slug": "rsa-key-generator"},
    {"name": "IPv4 / IPv6 Subnet Range Extractor", "slug": "ip-subnet-extractor"}
]

def run_batch_generation():
    config = load_json(CONFIG_PATH, {})
    api_key = config.get("api_key", "")
    if not api_key:
        log_message("[-] 错误：API 密钥未配置！请前往后台填写大模型 API Key。")
        return

    generated_registry = load_json(REGISTRY_PATH, {"tools": []})
    existing_slugs = [t["slug"] for t in generated_registry["tools"]]
    
    available_pool = [t for t in MASTER_TOOL_POOL if t["slug"] not in existing_slugs]
    
    if not available_pool:
        log_message("[-] 工具候选池已全部生成完毕！所有工具均已上线。")
        return

    batch_count = min(random.randint(3, 6), len(available_pool))
    selected_tools = random.sample(available_pool, batch_count)
    
    log_message(f"[*] 开始批量生成 {batch_count} 个新工具...")

    client = OpenAI(api_key=api_key, base_url=config.get("base_url", "https://api.deepseek.com"))
    ads_config = config.get("ads", {})

    for tool in selected_tools:
        slug = tool["slug"]
        name = tool["name"]
        log_message(f"[+] 正在生成工具: {name} ({slug})...")

        top_ad = ads_config.get("ad_top", {}).get("code", "") if ads_config.get("ad_top", {}).get("enabled") else ""
        sidebar_ad = ads_config.get("ad_sidebar", {}).get("code", "") if ads_config.get("ad_sidebar", {}).get("enabled") else ""
        bottom_ad = ads_config.get("ad_bottom", {}).get("code", "") if ads_config.get("ad_bottom", {}).get("enabled") else ""
        popup_ad = ads_config.get("ad_popup", {}).get("code", "") if ads_config.get("ad_popup", {}).get("enabled") else ""

        prompt = f"""
        Create a complete, responsive, single-file HTML page for the web tool: "{name}".
        Requirements:
        1. Use Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com"></script>`).
        2. Clean, modern, refreshing UI with dark/light mode compatibility.
        3. Fully functional vanilla JavaScript interactive logic for the tool.
        4. Insert this Top Ad code right below the header: {top_ad}
        5. Insert this Sidebar Ad code where appropriate: {sidebar_ad}
        6. Insert this Bottom Ad code above the footer: {bottom_ad}
        7. Insert any popup/script ad code right before closing body tag: {popup_ad}
        8. Provide clean SEO metadata description for developers.
        9. Return ONLY valid, executable HTML code. Do NOT wrap in markdown block ```html.
        """

        # 增加最多 3 次重试机制（应对 503 繁忙报错）
        success = False
        html_content = ""
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    timeout=60
                )
                html_content = response.choices[0].message.content.strip()
                success = True
                break
            except Exception as e:
                log_message(f"[-] 尝试 {attempt+1}/3 生成 {slug} 遇到错误: {e}，将在 5 秒后重试...")
                time.sleep(5)

        if not success:
            log_message(f"[-] 放弃生成 {slug}（连续 3 次请求失败）")
            continue

        if html_content.startswith("```html"):
            html_content = html_content[7:]
        if html_content.endswith("```"):
            html_content = html_content[:-3]

        tool_dir = os.path.join("static", "tools", slug)
        os.makedirs(tool_dir, exist_ok=True)
        with open(os.path.join(tool_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_content)

        generated_registry["tools"].append({
            "name": name,
            "slug": slug,
            "path": f"/static/tools/{slug}/index.html"
        })
        save_json(REGISTRY_PATH, generated_registry)
        log_message(f"[SUCCESS] 工具生成并保存成功: {slug}")
        
        # 每次生成后稍微歇息 2 秒，避免触发过快请求限流
        time.sleep(2)

    update_homepage_index(generated_registry, ads_config)
    
    if config.get("github_token") and config.get("github_repo"):
        auto_git_push(config)

def update_homepage_index(registry, ads_config):
    tools_list_html = ""
    for tool in registry["tools"]:
        tools_list_html += f"""
        <a href="{tool['path']}" class="p-6 bg-white rounded-xl shadow-sm border border-gray-100 hover:shadow-md transition duration-200 block">
            <h3 class="text-lg font-semibold text-gray-800">{tool['name']}</h3>
            <p class="text-sm text-gray-500 mt-2">点击进入在线使用 &rarr;</p>
        </a>
        """

    bottom_ad = ads_config.get("ad_bottom", {}).get("code", "") if ads_config.get("ad_bottom", {}).get("enabled") else ""

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DevOps Pro - Free Online Developer Tools Suite</title>
    <script src="[https://cdn.tailwindcss.com](https://cdn.tailwindcss.com)"></script>
</head>
<body class="bg-gray-50 min-h-screen text-gray-900">
    <header class="bg-white border-b border-gray-200 py-6">
        <div class="max-w-6xl mx-auto px-6 flex justify-between items-center">
            <h1 class="text-2xl font-bold text-blue-600">DevTools Hub</h1>
            <div class="flex items-center space-x-4">
                <span class="text-sm text-gray-500">Total Tools: {len(registry['tools'])}</span>
                <a href="/admin" class="text-xs bg-gray-100 hover:bg-gray-200 px-3 py-1.5 rounded-lg font-medium text-gray-700 transition">Admin Panel</a>
            </div>
        </div>
    </header>
    <main class="max-w-6xl mx-auto px-6 py-10">
        <div class="mb-8 text-center">
            <h2 class="text-3xl font-extrabold text-gray-800">Professional Developer & Ops Tools</h2>
            <p class="text-gray-500 mt-2">Fast, secure, and client-side web utility collection.</p>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            {tools_list_html}
        </div>
        <div class="mt-12">{bottom_ad}</div>
    </main>
</body>
</html>
    """
    os.makedirs("templates", exist_ok=True)
    with open("templates/index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

def auto_git_push(config):
    try:
        token = config.get("github_token")
        repo = config.get("github_repo")
        if not token or not repo:
            return
            
        remote_url = f"https://{token}@[github.com/](https://github.com/){repo}.git"
        
        res = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
        if res.returncode != 0:
            subprocess.run(["git", "remote", "add", "origin", remote_url], check=True)
        else:
            subprocess.run(["git", "remote", "set-url", "origin", remote_url], capture_output=True)
        
        subprocess.run(["git", "add", "."], check=True)
        
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if status_res.stdout.strip():
            subprocess.run(["git", "commit", "-m", "Auto-update incremental tool matrix"], check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], check=True)
            log_message("[+] 成功自动推送到 GitHub 仓库！")
    except Exception as e:
        log_message(f"[-] Git 推送跳过/失败: {e}")
