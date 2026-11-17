#!/usr/bin/env python3
"""scan.py 自测：在指定沙箱里造一个人工工程，校验各项检查并确认扫描全程无写入。

用法：python -X utf8 test_scan.py --sandbox <空目录或不存在的目录>
不要把真实工程目录当作 sandbox。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import scan  # noqa: E402

UVPROJX = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project><Targets><Target><TargetName>proj</TargetName><TargetOption><TargetCommonOption>
<OutputDirectory>.\\Objects\\</OutputDirectory><OutputName>proj</OutputName>
<ListingPath>.\\Listings\\</ListingPath></TargetCommonOption></TargetOption></Target></Targets></Project>
"""


def write(root, rel, text='x', size=None):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(b'\0' * size if size else text.encode('utf-8'))


def git(root, *args):
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-C', root, *args],
                   check=True, capture_output=True)


def build_project(p):
    write(p, 'proj.ioc', 'ioc')
    for i in range(12):
        write(p, f'Core/Src/m{i}.c', f'int f{i}(void){{return {i};}}\n')
    write(p, 'Core/Inc/version.h', '#define FW_VERSION "1.104.1"\n')
    write(p, 'Drivers/STM32/hal.c', 'hal')
    write(p, 'MDK-ARM/proj.uvprojx', UVPROJX)
    write(p, 'MDK-ARM/Objects/proj.hex', 'hex')
    write(p, 'MDK-ARM/Objects/main.o', 'o')
    write(p, 'MDK-ARM/Objects/proj_NFC_20260922.hex', 'old hex')
    write(p, 'MDK-ARM/build_pc13.log', 'log')
    write(p, 'MDK-ARM/build_a.log', 'log')
    write(p, 'MDK-ARM/build_b.log', 'log')
    write(p, 'src/Services/Diagnostics/diag.cs', 'class D {}')
    write(p, 'tools/verify_handoff_audit.py', 'print(1)')
    write(p, 'data/testrun/20260816-090400/logs/a.log', 'log')
    write(p, 'prisma/migrations/20260816090400_init/migration.sql', 'create table t();')
    for i in range(3):
        write(p, f'runlogs/x{i}.log', 'log')
    write(p, '_ref/demo/demo.uvprojx', UVPROJX)
    write(p, '_ref/demo/main.c', 'demo')
    write(p, '_ref/selfout/USER/self.uvprojx', UVPROJX.replace('.\\Objects\\', '.\\').replace('.\\Listings\\', '.\\'))
    write(p, '_ref/selfout/USER/main.c', 'x')
    write(p, 'sdk/Vendor_SDK(v3.1)20260529/vendor.sln', 'sln')
    write(p, 'sdk/Vendor_SDK(v3.1)20260529/lib.dll', 'dll')
    for d in ('20260901-a', '20260902-b', '20260903-c'):
        write(p, f'docs/releases/{d}/notes.md', d)
    write(p, 'evidence/w1/run.log', 'evidence')
    write(p, 'docs/视频流卡顿-二采判读-20260714.md', 'a')
    write(p, 'docs/视频流卡顿-三采判读-20260715.md', 'b')
    write(p, 'docs/通信协议规格书_V1.4.md', 'v1.4')
    write(p, 'docs/通信协议规格书_V1.10.md', 'v1.10')
    write(p, 'docs/协议核对报告.md', 'r')
    write(p, '开发方案-V1.0.html', '<p>plan</p>')
    write(p, 'index.html', '<html></html>')
    write(p, 'requirements.txt', 'x')
    write(p, '.gitignore', 'MDK-ARM/Objects/\n')
    write(p, 'VERSION', 'product = PROJ\nversion = 1.104.2\nhardware = 1\nprotocol = V1.4\n'
                        'protocol_doc = docs/通信协议规格书_V1.4.md\nversion_files = Core/Inc/version.h\n')
    for i in range(12):
        write(p, f'backup/proj_old/Core/Src/m{i}.c', f'int f{i}(void){{return {i};}}\n')
    write(p, 'backup/proj_old/proj.ioc', 'ioc')
    for i in range(12):
        write(p, f'variant_copy/Core/Src/m{i}.c', f'int f{i}(void){{return {i};}}\n')
    write(p, 'variant_copy/proj.ioc', 'ioc')
    write(p, '参考资料/LIB/.git/HEAD', 'ref: refs/heads/master\n')
    write(p, '参考资料/LIB/.git/config', '[remote "origin"]\n\turl = https://example.com/lib.git\n')
    write(p, '参考资料/LIB/lib.c', 'lib')

    git(p, 'init', '-b', 'main')
    git(p, 'add', '-A')
    git(p, 'commit', '-m', '初始')
    git(p, 'tag', 'v1.104.2')
    git(p, 'branch', 'protocol/v1.4')
    git(p, 'branch', 'codex/x')
    git(p, 'branch', 'acme')
    git(p, 'checkout', '-q', '-b', 'feature/y')
    write(p, 'Core/Src/extra.c', 'extra')
    git(p, 'add', '-A')
    git(p, 'commit', '-m', '新增: extra')
    git(p, 'checkout', '-q', 'main')
    write(p, 'Core/Src/m0.c', 'changed')
    git(p, 'commit', '-am', '修复: m0')
    # 未跟踪的杂物
    write(p, '.artifacts/task1/log.txt', 'tmp')
    write(p, 'big.bin', size=6 * 1024 * 1024)
    write(p, 'release/PROJ_V1.104.1.hex', 'rel')


IOC = 'ProjectManager.ProjectFileName={n}.ioc\nProjectManager.ProjectName={n}\n'
UVPROJX_REFS = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project><Targets><Target><TargetName>{n}</TargetName><TargetOption><TargetCommonOption>
<OutputDirectory>{n}\\</OutputDirectory><OutputName>{n}</OutputName></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><IncludePath>../Core/Inc{inc}</IncludePath></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups><Group><GroupName>Application/User/Core</GroupName><Files>
<File><FileName>main.c</FileName><FilePath>../Core/Src/main.c</FilePath></File>{files}
</Files></Group></Groups></Target></Targets></Project>
"""
GENERATED_C = '/* Copyright (c) 2026 STMicroelectronics. */\n/* USER CODE BEGIN 0 */\n/* USER CODE END 0 */\n'
README_TABLE = '# x\n\n## 目录结构\n\n| 路径 | 内容 |\n|---|---|\n{rows}\n\n## 其他\n'


def cubemx_board(p, base, name, user_app=False):
    write(p, f'{base}/{name}.ioc', IOC.format(n=name))
    write(p, f'{base}/Core/Src/main.c', GENERATED_C)
    write(p, f'{base}/Core/Src/system_stm32f1xx.c', '/* (c) STMicroelectronics */\nint s;\n')
    write(p, f'{base}/Drivers/hal.c', 'hal')
    files = ''
    if user_app:
        write(p, f'{base}/User/App/app.c', 'int app;\n')
        files = '<File><FileName>app.c</FileName><FilePath>../User/App/app.c</FilePath></File>'
    else:
        write(p, f'{base}/Core/Src/protocol.c', 'int proto;\n')
    write(p, f'{base}/MDK-ARM/{name}.uvprojx',
          UVPROJX_REFS.format(n=name, inc=';../User/App' if user_app else '', files=files))


def readme(p, paths):
    write(p, 'README.md', README_TABLE.format(rows='\n'.join(f'| `{x}` | 说明 |' for x in paths)))


def commit_all(p):
    git(p, 'init', '-b', 'main')
    git(p, 'add', '-A')
    git(p, 'commit', '-m', '初始')


def build_layout_projects(ws):
    # 1 CubeMX 套一层、tools/ 里混着上位机、根目录有中文启动脚本、未声明 layout
    p = os.path.join(ws, 'lay_nested')
    cubemx_board(p, 'Gimbal', 'Gimbal')
    write(p, 'tools/gui.py', 'import tkinter as tk\nimport hostlib\n')
    write(p, 'tools/hostlib.py', 'import serial\n')
    write(p, 'tools/frame_check.py', 'import sys\n')
    write(p, '启动上位机.bat', '@echo off\r\npython "tools\\gui.py"\r\n')
    write(p, 'VERSION', 'product = G\nversion = 0.200.0\nhardware = 0\nprotocol = -\n')
    readme(p, ['Gimbal/', 'tools/gui.py', 'gone/'])
    commit_all(p)
    # 2 已声明且合规
    p = os.path.join(ws, 'lay_ok')
    cubemx_board(p, 'firmware/Board', 'Board', user_app=True)
    write(p, 'firmware/Board/README.md', '# Board')
    write(p, 'host/gui.py', 'import tkinter\n')
    write(p, 'host/run_gui.bat', '@echo off\r\npython gui.py\r\n')
    write(p, 'tools/frame_check.py', 'import sys\n')
    write(p, 'docs/协议/P协议_V1.0.md', 'p')
    write(p, 'docs/方案/会议纪要.md', 'm')
    write(p, '.gitignore', 'release/\n')
    write(p, 'VERSION', 'product = OK\nversion = 0.100.0\nhardware = 0\nprotocol = V1.0\n'
                        'protocol_doc = docs/协议/P协议_V1.0.md\nlayout = 1\n')
    readme(p, ['firmware/Board/', 'host/', 'tools/', 'docs/协议/P协议_V1.0.md'])
    commit_all(p)
    # 3 pnpm 工作区：apps/ packages/ 是生态容器，compose.yml 应进 deploy/
    p = os.path.join(ws, 'lay_ws')
    write(p, 'pnpm-workspace.yaml', 'packages:\n  - apps/*\n')
    write(p, 'package.json', '{"name": "ws", "private": true}')
    write(p, 'apps/web/package.json', '{"dependencies": {"react": "18"}}')
    write(p, 'packages/ui/package.json', '{"name": "ui"}')
    write(p, 'compose.yml', 'services: {}\n')
    commit_all(p)
    # 4 ioc 名无意义，uvprojx 文件名与 ProjectName 不一致
    p = os.path.join(ws, 'lay_rename')
    cubemx_board(p, '.', 'xyz_test')
    os.rename(os.path.join(p, 'MDK-ARM', 'xyz_test.uvprojx'), os.path.join(p, 'MDK-ARM', 'real_only.uvprojx'))
    commit_all(p)


def check_layout(ws, check):
    r = scan.scan_project(os.path.join(ws, 'lay_nested'))
    b, c = r['phase_b'], r['counts']
    comps = {x['type']: x for x in b['components']}
    fw = comps.get('firmware-cubemx', {})
    check(fw.get('root') == 'Gimbal' and fw.get('expected') == 'firmware/Gimbal' and fw.get('ok') is False,
          f'套一层的 CubeMX 应建议迁到 firmware/Gimbal：{fw}')
    host = comps.get('host-python', {})
    check(host.get('files') == ['tools/gui.py', 'tools/hostlib.py'] and host.get('review'),
          f'tools/ 里的上位机及其本地依赖应识别为待复核的 host 组件：{host}')
    moves = {m['from']: m['to'] for m in b['moves']}
    check(moves.get('Gimbal/') == 'firmware/Gimbal/' and moves.get('tools/gui.py') == 'host/gui.py'
          and 'tools/frame_check.py' not in moves, f'迁移建议错误：{moves}')
    bat = next((v for v in b['root_violations'] if v['path'] == '启动上位机.bat'), {})
    check(bat.get('to') == 'host/', f'引用上位机的启动脚本应建议随 host 走：{bat}')
    core = [x for fi in b['firmware_internal'] for x in fi['core_user_files']]
    check(core == ['Gimbal/Core/Src/protocol.c'], f'Core 自写文件识别错误（system_stm32 不应算）：{core}')
    check(b['readme']['stale'] == ['gone'], f'README 目录表中已不存在的路径未识别：{b["readme"]}')
    check(b['declared'] is None and c['b'] == 0 and c['b_advice'] > 0, f'未声明 layout 时布局项只应算建议：{c}')

    r = scan.scan_project(os.path.join(ws, 'lay_ok'))
    b = r['phase_b']
    check(b['declared'] == 1 and b['issues'] == 0 and r['counts']['b'] == 0,
          '合规工程不应有布局项：' + str({k: b[k] for k in ('components', 'root_violations', 'unknown_dirs',
                                                         'readme', 'ioc_issues', 'firmware_internal')}))
    check(b['docs']['reclass_hints'] == [{'path': 'docs/方案/会议纪要.md', 'to': 'docs/记录/'}],
          f'方案里的会议纪要应提示归入记录（只提示不计入）：{b["docs"]["reclass_hints"]}')
    q = scan.scan_project(os.path.join(ws, 'lay_ok'), quick=True)
    check(not any(x.startswith('布局') for x in q['issues']), f'合规工程轻检不应报布局：{q["issues"]}')

    r = scan.scan_project(os.path.join(ws, 'lay_ws'))
    b = r['phase_b']
    check(b['workspace'] == 'node' and not b['components'] and not b['unknown_dirs'],
          f'pnpm 工作区的 apps/ packages/ 不应被当成待迁组件：{b["components"]} {b["unknown_dirs"]}')
    check(any(v['path'] == 'compose.yml' and 'deploy/' in v['hint'] for v in b['root_violations']),
          f'根目录 compose.yml 应建议进 deploy/：{b["root_violations"]}')

    r = scan.scan_project(os.path.join(ws, 'lay_rename'))
    ioc = '；'.join(r['phase_b']['ioc_issues'])
    check('硬约束' in ioc and '看不出含义' in ioc, f'ioc 命名问题未识别：{ioc}')
    moves = {m['from'] for m in r['phase_b']['moves']}
    check({'xyz_test.ioc', 'Core/', 'Drivers/', 'MDK-ARM/'} <= moves, f'铺在根目录的 CubeMX 应逐项迁移：{moves}')


def check_layout_regression(ws, check):
    """已声明工程出现回退时轻检要拦下（在快照比对之后执行，会改动沙箱）。"""
    p = os.path.join(ws, 'lay_ok')
    write(p, 'run.bat', '@echo off\r\n')
    write(p, 'misc/a.txt', 'a')
    git(p, 'add', '-A')
    git(p, 'commit', '-m', '回退')
    q = scan.scan_project(p, quick=True)
    lay = [x for x in q['issues'] if x.startswith('布局')]
    check(any('run.bat' in x for x in lay) and any('misc/' in x for x in lay)
          and any('README 目录表未说明 misc/' in x for x in lay), f'轻检未拦下布局回退：{lay}')


DOCS_README = ('# 演示\n\n## 目录结构\n\n| 路径 | 内容 |\n|---|---|\n{rows}\n\n'
               '## 权威文档\n\n- [运营方案](docs/方案/运营方案.md)\n\n## 素材取用\n\n- 头像：`assets/定稿/01_角色/a.png`\n')


def build_docs_projects(ws):
    # 1 未声明、未整理的文档/素材型工程
    p = os.path.join(ws, 'docs_raw')
    write(p, '演示-运营方案.md', '# 运营方案\n')
    write(p, '草图.png', 'png0')
    write(p, '演示_定稿素材_20261027/01_角色/a.png', 'PNG-A')
    write(p, '演示_定稿素材_20261027/SHA256SUMS.txt', 'x  a.png\n')
    write(p, '演示_定稿素材_20261027/10_记录/定稿素材清单.json', '{}')
    write(p, '旧包_20261020/a.png', 'PNG-A')
    write(p, 'IP设定/源码/gen.py', 'print(1)\n')
    write(p, 'README.md', '# 演示\n')
    # 2 已声明且合规
    p = os.path.join(ws, 'docs_ok')
    write(p, 'VERSION', 'type = docs\nname = 演示\nlayout = 1\n')
    rows = ['docs/方案/', 'docs/需求/', 'docs/记录/', 'docs/报告/', 'docs/协议/', 'assets/定稿/', 'assets/原图/',
            'assets/参考/', 'tools/', 'release/']
    write(p, 'README.md', DOCS_README.format(rows='\n'.join(f'| `{x}` | 说明 |' for x in rows)))
    write(p, 'CLAUDE.md', '# 演示')
    write(p, 'docs/方案/运营方案.md', '> v1.1（2026-10-27）：旧包在 `旧包_20261027/x.png`\n\n'
                                  '见 [需求](../需求/生图需求.md)，素材 `assets/定稿/01_角色/a.png`\n')
    write(p, 'docs/需求/生图需求.md', '上传 `assets/参考/ref.png`；定稿图指 `assets/定稿/` 下的：\n\n'
                                  '| T1 | `01_角色/a.png` |\n\n模板以后放 `docs/模板/`\n')
    write(p, 'docs/记录/推进记录.md', '当时在 `旧目录/x.png`\n')
    write(p, 'docs/报告/周报/2026-W44.md', 'w44')
    write(p, 'docs/协议/串口协议_V1.0.md', 'p')
    write(p, 'assets/定稿/00_素材浏览.html',
          '<a href="01_%E8%A7%92%E8%89%B2/a.png">a</a><a href="https://x.com">x</a><a href="#top">t</a>')
    write(p, 'assets/定稿/01_角色/a.png', 'PNG-A')
    write(p, 'assets/原图/raw.png', 'RAW')
    write(p, 'assets/参考/ref.png', 'REF')
    write(p, 'tools/gen.py', 'print(1)\n')
    write(p, 'release/演示_定稿素材_20261028.zip', 'zip')


def check_docs(ws, check):
    r = scan.scan_project(os.path.join(ws, 'docs_raw'), docs=True)
    a, b, c = r['phase_a'], r['phase_b'], r['counts']
    check(r.get('type') == 'docs' and b['declared'] is None and c['b'] == 0 and c['b_advice'] > 0
          and not a['not_git'], f'未声明的文档型工程：布局项只算建议、不报非 git：{c}')
    hints = {u['path']: u['hint'] for u in b['unknown_dirs']}
    check('素材包' in hints.get('演示_定稿素材_20261027/', '') and '素材包' in hints.get('旧包_20261020/', '')
          and 'assets/参考/' in hints.get('IP设定/', ''), f'词表外目录提示错误：{hints}')
    viol = {v['path']: v['hint'] for v in b['root_violations']}
    check('docs/' in viol.get('演示-运营方案.md', '') and 'assets/' in viol.get('草图.png', ''),
          f'根目录散放文档/图片提示错误：{viol}')
    check(any(set(g['files']) == {'演示_定稿素材_20261027/01_角色/a.png', '旧包_20261020/a.png'}
              for g in b['duplicates']), f'内容相同的素材未识别：{b["duplicates"]}')
    check(set(b['checksums']) == {'演示_定稿素材_20261027/SHA256SUMS.txt', '演示_定稿素材_20261027/10_记录/定稿素材清单.json'},
          f'校验和与清单识别错误：{b["checksums"]}')
    check(not b['readme']['has_table'] and 'type = docs' in '；'.join(r['phase_c']['version']['issues']),
          f'README 目录表与 VERSION 提示缺失：{b["readme"]} {r["phase_c"]["version"]}')

    r = scan.scan_project(os.path.join(ws, 'docs_ok'))
    b = r['phase_b']
    detail = {k: b[k] for k in ('root_violations', 'unknown_dirs', 'dated_dirs', 'docs', 'assets', 'checksums',
                                'sub_readmes', 'duplicates', 'links', 'readme')}
    check(r.get('type') == 'docs' and b['declared'] == 1 and b['issues'] == 0 and r['counts']['c'] == 0,
          f'合规文档型工程不应有布局/规范项（VERSION 声明后自动识别）：{detail} {r["phase_c"]}')
    check(not b['links']['review'], f'版本历史行与记录类文档里的旧路径不应复核：{b["links"]["review"]}')
    q = scan.scan_project(os.path.join(ws, 'docs_ok'), quick=True)
    check(q['issues'] == [], f'合规文档型工程轻检不应报问题：{q["issues"]}')


def check_docs_regression(ws, check):
    """已声明的文档型工程出现回退时轻检要拦下（在快照比对之后执行，会改动沙箱）。"""
    p = os.path.join(ws, 'docs_ok')
    write(p, 'note.md', 'n')
    write(p, 'misc/a.txt', 'a')
    write(p, 'docs/杂项.md', 'x')
    write(p, 'assets/x.png', 'x')
    write(p, 'release/演示_定稿素材_20261001.zip', 'old')
    write(p, 'docs/方案/运营方案.md', '见 [坏链接](../方案/不存在.md)，素材 `assets/定稿/99_没有/x.png`\n')
    q = scan.scan_project(p, quick=True)
    text = '；'.join(q['issues'])
    check(all(k in text for k in ('note.md', 'misc/', 'docs/杂项.md', 'assets/x.png', '只留最近一次')),
          f'文档型轻检未拦下回退：{q["issues"]}')
    r = scan.scan_project(p)
    links = r['phase_b']['links']
    check(any(x['target'] == '../方案/不存在.md' for x in links['broken'])
          and [x['path'] for x in links['review']] == ['assets/定稿/99_没有/x.png'],
          f'失效链接或反引号失效路径识别错误：{links}')


def section_words(doc: str, heading: str) -> set[str]:
    """参考文档里某一节表格第一列的 `xxx/` 词。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references', doc)
    m = open(path, encoding='utf-8').read().split(heading, 1)
    words = set()
    if len(m) == 2:
        for line in m[1].split('\n## ', 1)[0].splitlines():
            cells = [x.strip() for x in line.strip().strip('|').split('|')]
            if line.startswith('|') and cells and cells[0].startswith('`') and cells[0].endswith('/`'):
                words.add(cells[0].strip('`/'))
    return words


def check_vocab_doc(check):
    for doc, heading, words in (('目录布局.md', '## 2. 全局词表', set(scan.LAYOUT_V1)),
                                ('文档整理.md', '## 2. 顶层词表', set(scan.DOCS_LAYOUT_V1)),
                                ('文档整理.md', '## 3. docs/ 十类', set(scan.DOC_CATEGORIES)),
                                ('文档整理.md', '## 4. assets/ 三个阶段', set(scan.ASSET_STAGES))):
        got = section_words(doc, heading)
        check(got == words, f'references/{doc}「{heading}」与 scan.py 不一致：{got ^ words}')


def snapshot(root):
    snap = {}
    for d, _, files in os.walk(root):
        for f in files:
            fp = os.path.join(d, f)
            st = os.stat(fp)
            snap[fp] = (st.st_size, st.st_mtime_ns)
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sandbox', required=True)
    sandbox = os.path.abspath(ap.parse_args().sandbox)
    if os.path.exists(sandbox) and os.listdir(sandbox):
        sys.exit(f'sandbox 必须为空：{sandbox}')
    ws = os.path.join(sandbox, 'ws')
    proj = os.path.join(ws, 'proj')
    build_project(proj)
    shutil.copytree(proj, os.path.join(ws, 'proj_copy'), ignore=shutil.ignore_patterns('.git'))
    write(ws, '.hidden_store/state.json', '{}')
    build_layout_projects(ws)
    build_docs_projects(ws)

    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    before = snapshot(ws)
    r = scan.scan_project(proj)
    a, b, pc = r['phase_a'], r['phase_b'], r['phase_c']
    cands = {c['path']: c for c in a['candidates']}
    check('.artifacts/' in cands and cands['.artifacts/']['category'] == 'B'
          and cands['.artifacts/']['tracked'] == 'none', '.artifacts/ 应为 B 类未跟踪')
    check('evidence/' in cands and cands['evidence/']['tracked'] == 'all', 'evidence/ 应为已跟踪 A 类')
    check('backup/' in cands and cands['backup/']['contains_project'], 'backup/ 应为 C 类且标注内含工程')
    check('MDK-ARM/build_pc13.log' in cands, '构建日志应被列出')
    check('MDK-ARM/' not in cands, '含工程入口的 MDK-ARM/ 不应被折叠成候选')
    check(not any('Diagnostics' in k for k in cands), '源码树中的 Diagnostics/ 不应被列出')
    check(not any('verify_handoff' in k for k in cands), '工具脚本不应按交接文件列出')
    check('data/testrun/20260816-090400/' in cands, '带日期的运行目录应被列出')
    check(not any('migrations' in k for k in cands), '数据库迁移目录不应被列出')
    check('runlogs/' in cands and cands['runlogs/']['files'] == 3, '同目录多个日志应折叠为目录候选')
    check(any(x['path'] == '_ref/demo' for x in a['reference_projects']), '参考工程 _ref/demo 未识别')
    check(any(x['path'] == '_ref/selfout' for x in a['reference_projects']), '输出目录为 .\\ 的参考工程未识别')
    check(not any(k.startswith('sdk/') for k in cands), '带日期的厂商 SDK 不应按运行目录列出')
    check('docs/releases/' in cands and not any(k.startswith('docs/releases/2026') for k in cands),
          '同级多个日期目录应合并为 docs/releases/')
    check('MDK-ARM/Objects/proj_NFC_20260922.hex' in cands, 'Keil 输出目录中的旧固件应被列出')
    check(not any(k.startswith('MDK-ARM/Objects/proj.hex') or k == 'MDK-ARM/Objects/' for k in cands),
          '当前构建产物不应被列出')
    check(any(g['topic'] == '视频流卡顿' and len(g['files']) == 2 for g in a['doc_groups']), '同主题文档组缺失')
    vers = {s['version'] for s in r['spec_docs'] if s['topic'] == '通信协议规格书'}
    check(vers == {'1.4', '1.10'}, f'规范本体版本识别错误：{vers}')
    check(not any('协议核对报告' in s['path'] for s in r['spec_docs']), '过程文档不应算规范本体')
    check(any(c['path'] == 'variant_copy' and c['strength'] == '强' for c in a['copies']), '副本 variant_copy 未识别')
    check(any(n['path'] == '参考资料/LIB' and 'example.com' in n['remote'] for n in a['nested_repos']), '嵌套仓库未识别')
    check(any(x['path'] == 'big.bin' for x in a['big_untracked']), '未跟踪大文件未识别')
    names = {x['name']: x for x in pc['branches']}
    check(set(names) == {'codex/x', 'feature/y', 'acme'}, f'分支问题集合错误：{set(names)}')
    check(names.get('codex/x', {}).get('merged'), 'codex/x 应为已合并')
    check(names.get('feature/y', {}).get('ahead') == 1, 'feature/y 应领先 1 个提交')
    vi = '；'.join(pc['version']['issues'])
    check('version.h' in vi and '冻结' in vi, f'VERSION 问题缺失：{vi}')
    rel = '；'.join(pc['release'])
    check('文件名与 VERSION 不符' in rel and '.gitignore' in rel, f'release 问题缺失：{rel}')
    check(b['docs']['root_docs'] == ['开发方案-V1.0.html'], f'根目录散放文档识别错误：{b["docs"]["root_docs"]}')
    check('mdk-arm/objects' in r['protected'], f'Keil 输出目录未受保护：{r["protected"]}')
    check(not any(m['from'].startswith(('backup', 'variant_copy', '_ref', '参考资料')) for m in b['moves']),
          f'阶段 A 已认领的目录不应出现在布局迁移里：{b["moves"]}')
    check_layout(ws, check)
    check_docs(ws, check)
    check_vocab_doc(check)

    t0 = time.time()
    q = scan.scan_project(proj, quick=True)
    qt = time.time() - t0
    check(any('.artifacts/' in x for x in q['issues']), f'轻检未发现 .artifacts/：{q["issues"]}')
    check(qt < 3, f'轻检耗时 {qt:.2f}s 超过 3 秒')

    w = scan.scan_workspace([ws])
    check(any({os.path.basename(c['a']), os.path.basename(c['b'])} == {'proj', 'proj_copy'} for c in w['copies']),
          '跨工程副本未识别')
    check(any(x['path'].endswith('.hidden_store') for x in w['clutter']), '工作区隐藏目录未识别')
    check(any(p['name'] == 'proj_copy' and p['phase_a']['not_git'] for p in w['projects']), '非 git 工程未识别')
    check(any(p['name'] == 'docs_ok' and p.get('type') == 'docs' for p in w['projects']), '工作区里的文档型工程未自动识别')
    for p in w['projects']:
        scan.render_project(p)
    scan.render_workspace(w)
    scan.render_quick(q)

    after = snapshot(ws)
    check(before == after, '扫描期间文件被修改：'
          + '、'.join(sorted(set(before.items()) ^ set(after.items()), key=str)[:5].__repr__().split(',')[:3]))
    check_layout_regression(ws, check)
    check_docs_regression(ws, check)

    if fails:
        print('失败：')
        for f in fails:
            print(' -', f)
        return 1
    print(f'全部通过（轻检 {qt:.2f}s）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
