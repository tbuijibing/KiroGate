import re

with open("kiro_gateway/routes.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace all '= get_current_user(request)' with '= await get_current_user(request)'
content = content.replace("= get_current_user(request)", "= await get_current_user(request)")

with open("kiro_gateway/routes.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Done")
