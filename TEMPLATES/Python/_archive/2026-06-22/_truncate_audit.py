import re

with open("audit_v5_run_20260612.py", encoding="utf-8") as f:
    content = f.read()

# Keep everything up to and including the if __name__ guard + run() call
pattern = r'(if __name__ == "__main__":\n    run\(\)\n)'
m = re.search(pattern, content)
if m:
    clean = content[:m.end()]
    with open("audit_v5_run_20260612.py", "w", encoding="utf-8") as f:
        f.write(clean)
    print(f"Truncated. File is now {len(clean)} chars, {len(clean.splitlines())} lines.")
else:
    print("Pattern not found")
