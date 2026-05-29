import os
import re
import emoji

def remove_emojis_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # replace emojis with empty string
    new_content = emoji.replace_emoji(content, replace='')
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Removed emojis from {filepath}")

for root, dirs, files in os.walk('frontend'):
    for file in files:
        if file.endswith('.html') or file.endswith('.js') or file.endswith('.css'):
            remove_emojis_from_file(os.path.join(root, file))

for root, dirs, files in os.walk('backend'):
    for file in files:
        if file.endswith('.py') or file.endswith('.html'):
            remove_emojis_from_file(os.path.join(root, file))
