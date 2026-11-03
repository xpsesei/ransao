import os
import threading
import time
from flask import Flask, render_template, request, redirect, url_for, jsonify
from generator import run_batch_generation, CONFIG_PATH, LOG_PATH, load_json, save_json

app = Flask(__name__)
is_running = False

def background_worker():
    global is_running
    while is_running:
        run_batch_generation()
        # 读取用户在后台设置的运行间隔（小时），默认 6 小时
        config = load_json(CONFIG_PATH, {})
        interval_hours = int(config.get("interval_hours", 6))
        sleep_seconds = interval_hours * 3600
        
        for _ in range(sleep_seconds):
            if not is_running:
                break
            time.sleep(1)

@app.route("/")
def home():
    registry = load_json("registry.json", {"tools": []})
    return render_template("index.html", tools=registry.get("tools", []))

@app.route("/admin", methods=["GET"])
def admin_panel():
    config = load_json(CONFIG_PATH, {})
    logs = ""
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
            logs = "".join(lines[-50:])
    return render_template("admin.html", config=config, logs=logs, status="running" if is_running else "stopped")

@app.route("/admin/save", methods=["POST"])
def save_config():
    config = load_json(CONFIG_PATH, {})
    config["api_key"] = request.form.get("api_key", "")
    config["base_url"] = request.form.get("base_url", "https://api.deepseek.com")
    config["github_token"] = request.form.get("github_token", "")
    config["github_repo"] = request.form.get("github_repo", "")
    config["interval_hours"] = int(request.form.get("interval_hours", 6))

    for ad_key in ["ad_top", "ad_sidebar", "ad_bottom", "ad_popup"]:
        if ad_key not in config:
            config["ads"] = config.get("ads", {})
            config["ads"][ad_key] = {}
        config["ads"][ad_key]["enabled"] = True if request.form.get(f"{ad_key}_enabled") == "on" else False
        config["ads"][ad_key]["code"] = request.form.get(f"{ad_key}_code", "")

    save_json(CONFIG_PATH, config)
    return redirect(url_for("admin_panel"))

@app.route("/admin/toggle", methods=["POST"])
def toggle_automation():
    global is_running
    action = request.json.get("action")
    if action == "start" and not is_running:
        is_running = True
        threading.Thread(target=background_worker, daemon=True).start()
        return jsonify({"status": "started"})
    elif action == "stop":
        is_running = False
        return jsonify({"status": "stopped"})
    return jsonify({"status": "no_change"})

if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    print("[*] Starting Flask server on port 5050...")
    app.run(host="0.0.0.0", port=5050, debug=False)
