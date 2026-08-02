import os
import sys
from pathlib import Path

import requests

OPENCODE_API_KEY = os.environ.get("OPENCODE_API_KEY", "")
MODEL_NAME = os.environ.get("OPENCODE_MODEL", "opencode-zen/deepseek-v4-flash-free")

FILES_TO_AUDIT = [
    "bot/number_manager.py",
    "bot/whatsapp_fix.py",
    "wa_checker.js"
]

def call_opencode_zen(system_prompt: str, user_prompt: str) -> str:
    url = "https://opencode.ai/zen/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENCODE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    if r.status_code != 200:
        raise Exception(f"OpenCode Zen returned {r.status_code}: {r.text}")  # noqa: TRY002
    return r.json()["choices"][0]["message"]["content"]

def main():
    if not OPENCODE_API_KEY:
        print("❌ Error: OPENCODE_API_KEY env variable is required.")
        sys.exit(1)

    print(f"🤖 Starting OpenCode Zen AI Audit using model: {MODEL_NAME}...")

    for file_path_str in FILES_TO_AUDIT:
        path = Path(file_path_str)
        if not path.exists():
            print(f"⚠️ Skip: {file_path_str} tidak ditemukan.")
            continue

        print(f"🔍 Auditing {file_path_str}...")
        code = path.read_text(encoding="utf-8")

        system_prompt = (
            "You are an expert software engineer AI. Your task is to audit the given code for syntax errors, "
            "runtime exceptions, bugs, and edge cases. If you find any issues, improve the code and rewrite the "
            "entire file. Return ONLY the complete rewritten code. Do not include markdown block markers like ```python or ```javascript or explanations."
        )

        user_prompt = f"File: {file_path_str}\n\nCode:\n{code}"

        try:
            improved_code = call_opencode_zen(system_prompt, user_prompt).strip()

            # Remove any markdown format wrappers if the model still outputs them
            if improved_code.startswith("```"):
                lines = improved_code.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                improved_code = "\n".join(lines).strip()

            if improved_code and len(improved_code) > 20:
                if improved_code != code.strip():
                    print(f"✅ Changes detected for {file_path_str}. Saving improvements...")
                    path.write_text(improved_code, encoding="utf-8")
                else:
                    print(f"ℹ️ {file_path_str} is already optimal. No changes needed.")
            else:
                print(f"⚠️ Skip: Received empty or invalid response for {file_path_str}.")
        except Exception as e:  # noqa: BLE001
            print(f"❌ Error auditing {file_path_str}: {e}")

if __name__ == "__main__":
    main()
