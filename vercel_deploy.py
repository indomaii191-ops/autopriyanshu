import os
import time
import random
import requests
import base64
import logging
from config import VERCEL_API_TOKEN, VERCEL_TEAM_ID

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _project_exists(page_name):
    url = f"https://api.vercel.com/v9/projects/{page_name}"
    headers = {"Authorization": f"Bearer {VERCEL_API_TOKEN}"}
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    try:
        r = requests.get(url, headers=headers, params=params)
        return r.status_code == 200
    except Exception:
        return False


def _generate_name_candidates(base_name):
    yield base_name
    for i in range(2, 10):
        yield f"{base_name}-{i}"
    seen = set()
    for _ in range(50):
        rand = random.randint(100, 999)
        if rand in seen:
            continue
        seen.add(rand)
        yield f"{base_name}-{rand}"


def _find_available_name(base_name):
    for candidate in _generate_name_candidates(base_name):
        if not _project_exists(candidate):
            return candidate
    return f"{base_name}-{int(time.time()) % 100000}"


def delete_vercel_project(page_name):
    url = f"https://api.vercel.com/v9/projects/{page_name}"
    headers = {"Authorization": f"Bearer {VERCEL_API_TOKEN}"}
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    response = requests.delete(url, headers=headers, params=params)
    return response.status_code in [200, 204, 404]


def _wait_for_ready(deployment_id, timeout=120):
    url = f"https://api.vercel.com/v13/deployments/{deployment_id}"
    headers = {"Authorization": f"Bearer {VERCEL_API_TOKEN}"}
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url, headers=headers, params=params)
            if r.status_code == 200:
                data = r.json()
                state = data.get("readyState") or data.get("status")
                if state == "READY":
                    return True, data
                if state in ("ERROR", "CANCELED"):
                    return False, data
        except Exception:
            pass
        time.sleep(3)
    return False, {"readyState": "TIMEOUT"}


def _try_deploy(page_name, files_list):
    url = "https://api.vercel.com/v13/deployments"
    headers = {
        "Authorization": f"Bearer {VERCEL_API_TOKEN}",
        "Content-Type": "application/json"
    }
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID

    payload = {
        "name": page_name,
        "files": files_list,
        "projectSettings": {"framework": None, "buildCommand": None, "outputDirectory": None},
        "target": "production"
    }
    response = requests.post(url, headers=headers, params=params, json=payload)
    if response.status_code not in [200, 201]:
        raise Exception(f"Vercel deploy failed: {response.text}")

    data = response.json()
    deployment_id = data.get("id")
    returned_url = data.get("url", "")

    ready, final_data = _wait_for_ready(deployment_id)
    state = final_data.get("readyState") or final_data.get("status")
    final_aliases = final_data.get("alias") or []

    return {
        "deployment_id": deployment_id,
        "ready": ready,
        "state": state,
        "returned_url": returned_url,
        "aliases": final_aliases,
        "project_name": page_name,
    }


def _try_add_alias(page_name, deployment_url):
    if not deployment_url:
        return False
    dep = deployment_url.replace("https://", "").replace("http://", "")
    url = f"https://api.vercel.com/v2/deployments/{dep}/aliases"
    headers = {"Authorization": f"Bearer {VERCEL_API_TOKEN}"}
    params = {}
    if VERCEL_TEAM_ID:
        params["teamId"] = VERCEL_TEAM_ID
    payload = {"alias": f"{page_name}.vercel.app"}
    try:
        r = requests.post(url, headers=headers, params=params, json=payload)
        return r.status_code in [200, 201]
    except Exception:
        return False


def deploy_html_to_vercel(requested_name, files_list):
    for candidate in _generate_name_candidates(requested_name):
        try:
            result = _try_deploy(candidate, files_list)
        except Exception:
            continue

        if not result["ready"]:
            try:
                delete_vercel_project(candidate)
            except Exception:
                pass
            continue

        target_url = f"https://{candidate}.vercel.app"

        if result.get("returned_url") == f"{candidate}.vercel.app":
            return {
                "url": target_url,
                "clean": True,
                "ready": True,
                "state": result["state"],
                "actual_name": candidate,
                "renamed": candidate != requested_name,
            }

        aliases = result.get("aliases") or []
        if any(isinstance(a, str) and a == f"{candidate}.vercel.app" for a in aliases):
            return {
                "url": target_url,
                "clean": True,
                "ready": True,
                "state": result["state"],
                "actual_name": candidate,
                "renamed": candidate != requested_name,
            }

        if _try_add_alias(candidate, result["returned_url"]):
            return {
                "url": target_url,
                "clean": True,
                "ready": True,
                "state": result["state"],
                "actual_name": candidate,
                "renamed": candidate != requested_name,
            }

        try:
            delete_vercel_project(candidate)
        except Exception:
            pass
        time.sleep(2)

    raise Exception("Could not find an available Vercel name — try a different name")


def collect_theme_files(template_dir, html_overrides=None):
    files_list = []
    html_overrides = html_overrides or {}
    for root, dirs, files in os.walk(template_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for file in files:
            if file.startswith('.'):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, template_dir).replace("\\", "/")
            if rel_path in html_overrides:
                content = html_overrides[rel_path]
                if isinstance(content, str):
                    content = content.encode('utf-8')
                files_list.append({
                    "file": rel_path,
                    "data": base64.b64encode(content).decode('utf-8'),
                    "encoding": "base64"
                })
                continue
            try:
                with open(full_path, 'rb') as f:
                    file_data = f.read()
                files_list.append({
                    "file": rel_path,
                    "data": base64.b64encode(file_data).decode('utf-8'),
                    "encoding": "base64"
                })
            except Exception:
                pass
    return files_list


def create_vercel_page(page_name, theme, apk_url, app_display_name=None):
    if app_display_name is None:
        app_display_name = page_name.replace('-', ' ').title()
    template_dir = os.path.join(BASE_DIR, "templates", theme)
    template_path = os.path.join(template_dir, "index.html")
    if not os.path.exists(template_path):
        raise Exception(f"Template not found: {template_path}")
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    html_content = html_content.replace("{{APP_NAME}}", app_display_name)
    html_content = html_content.replace("{{APK_URL}}", apk_url)
    files_list = collect_theme_files(template_dir, html_overrides={"index.html": html_content})
    return deploy_html_to_vercel(page_name, files_list)


def create_vercel_page_with_url(page_name, theme, apk_url, app_display_name=None):
    return create_vercel_page(page_name, theme, apk_url, app_display_name)
