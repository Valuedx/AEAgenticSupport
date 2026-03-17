
import os
import sys

def search_files():
    targets = ["Start Date", "Any other relevant information"]
    print(f"Searching for {targets}")
    for root, dirs, files in os.walk("D:\\AEAgenticSupport"):
        if any(x in root for x in [".git", "__pycache__", "site-packages", "venv", ".next", "node_modules"]):
            continue
        for file in files:
            if not file.endswith(('.py', '.json', '.md', '.txt', '.html')):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    for target in targets:
                        if target in content:
                            print(f"FOUND '{target}' in {path}")
            except:
                pass

if __name__ == "__main__":
    search_files()
