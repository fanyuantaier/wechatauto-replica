"""初始化本地配置和空表；绝不覆盖已有配置或运行数据。"""
import json
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
TABLES = {'data/aliases.json': {'global': {}, 'groups': {}, 'auto': {}}, 'data/memory.json': {'version': 1, 'people': {}}, 'stickers/stickers.json': {'threshold': 0.75, 'cell_pad': 4, 'labels': {}}, 'stickers/labels_preview.json': {}, 'stickers/md5_labels.json': {}}
def initialize():
    config = BASE / "config.json"
    if not config.exists():
        shutil.copyfile(BASE / "config.example.json", config)
    for name, value in TABLES.items():
        p = BASE / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
if __name__ == "__main__":
    initialize()
    print("本地配置已初始化，请自行填写配置；不要提交 config.json、data 或媒体文件。")
