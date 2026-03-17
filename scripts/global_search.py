
import os

def search_files(directory, search_str):
    print(f"Searching for '{search_str}' in {directory}...")
    for root, dirs, files in os.walk(directory):
        if ".git" in root or "__pycache__" in root:
            continue
        for file in files:
            file_path = os.path.join(root, file)
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if search_str in content:
                        print(f"MATCH FOUND: {file_path}")
            except Exception as e:
                pass

if __name__ == "__main__":
    search_files("D:\\AEAgenticSupport", "is Mandatory")
    search_files("D:\\AEAgenticSupport", "Any other relevant information")
