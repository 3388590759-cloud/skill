#!/usr/bin/env python3
"""把仓库里的一个 skill 目录打成 Release 安装包 dist/<skill>-v<版本>.skill。

安装包是 zip：顶层是 <skill>/ 文件夹，中文文件名按 UTF-8 编码（带 UTF-8 标志位），
在任何系统上解压都不会乱码。只打包 git 已跟踪的文件，.gitignore 排除的本机私有文件不会进包。

用法（在仓库根目录）：
    python -X utf8 tools/pack_skill.py one-project 1.0.0
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args: str) -> str:
    out = subprocess.run(['git', '-c', 'core.quotepath=false', '-C', REPO, *args],
                         check=True, capture_output=True)
    return out.stdout.decode('utf-8')


def main() -> int:
    ap = argparse.ArgumentParser(description='打包 skill 为 .skill 安装包')
    ap.add_argument('skill', help='skill 目录名，如 one-project')
    ap.add_argument('version', help='版本号，如 1.0.0')
    a = ap.parse_args()

    if not re.fullmatch(r'\d+\.\d+\.\d+', a.version):
        sys.exit(f'版本号应为 主.次.修订，如 1.0.0：{a.version}')
    if not os.path.isfile(os.path.join(REPO, a.skill, 'SKILL.md')):
        sys.exit(f'找不到 {a.skill}/SKILL.md')

    dirty = git('status', '--porcelain', '--', a.skill).strip()
    if dirty:
        sys.exit(f'{a.skill}/ 有未提交的改动，先提交再打包：\n{dirty}')

    files = sorted(f for f in git('ls-files', '-z', '--', a.skill).split('\0') if f)
    out_dir = os.path.join(REPO, 'dist')
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f'{a.skill}-v{a.version}.skill')
    if os.path.exists(out):
        sys.exit(f'已存在，不覆盖：{out}')

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel in files:
            z.write(os.path.join(REPO, rel), rel)  # 非 ASCII 文件名 zipfile 会自动置 UTF-8 标志位
    print(f'{out}（{len(files)} 个文件，{os.path.getsize(out) / 1024:.1f} KB）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
