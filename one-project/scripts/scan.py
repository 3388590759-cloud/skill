#!/usr/bin/env python3
"""one-project 只读扫描器：只报告事实，不写任何文件，不改变 git 状态。

用法：
  python -X utf8 scan.py <工程路径> [--quick] [--json]
  python -X utf8 scan.py <工程路径> --docs [--quick] [--json]     # 未声明的文档/素材型工程按文档规则扫描
  python -X utf8 scan.py --workspace <根目录> [<根目录> ...] [--json]

VERSION 声明 type = docs 的工程自动按文档/素材型规则扫描（references/文档整理.md）。

退出码：0 无待处理项；1 有待处理项；2 扫描出错。
语义判断（是否真是过程文件、哪份是最终版）交给调用方 AI，这里只给证据。
"""

from __future__ import annotations

import argparse
import bisect
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

BIG_FILE = 5 * 1024 * 1024
PRUNE_NAMES = {'.git', 'node_modules'}
CACHE_NAMES = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.gradle', '.pio', '.vs'}
VENDOR_NAMES = {'drivers', 'middlewares', 'rte', 'node_modules', 'packages', 'third_party', 'thirdparty',
                'vendor', 'bin', 'obj', 'dist', 'build', 'objects', 'listings', '__pycache__', '.venv', 'venv'}
SPEC_DIR_NAMES = {'contracts', 'contract', '协议', '接口', '需求', '参考资料', '参考文档', '开发资料', 'datasheet',
                  'datasheets'}
DOC_CATEGORIES = ('协议', '接口', '需求', '参考资料', '方案', '问题记录', '记录', '手册', '报告', '模板')
BIN_EXT = {'.exe', '.msi', '.hex', '.bin', '.axf', '.elf', '.zip', '.7z', '.rar', '.tar', '.gz', '.iso', '.img',
           '.dll', '.so', '.pdb', '.apk'}
DOC_EXT = {'.md', '.html', '.htm', '.docx', '.doc', '.pdf', '.txt', '.xlsx', '.xls', '.pptx', '.drawio'}
SRC_EXT = {'.c', '.h', '.cpp', '.hpp', '.cc', '.s', '.py', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.vue', '.cs',
           '.java', '.kt', '.go', '.rs', '.ioc', '.uvprojx', '.uvoptx', '.json', '.xml', '.html', '.css', '.scss',
           '.bat', '.ps1', '.sh', '.ini', '.yml', '.yaml', '.toml', '.cfg', '.md'}
ROOT_ALLOWED = re.compile(r'(readme.*|claude\.md|agents\.md|version|license.*|changelog.*|contributing.*'
                          r'|requirements.*\.txt|cmakelists\.txt|.*\.lock\.txt|robots\.txt)', re.I)
CJK_RE = re.compile(r'[一-鿿]')
BRANCH_OK = re.compile(r'(main|protocol/v\d+\.\d+(-[a-z0-9]+)*)')
TASK_BRANCH = re.compile(r'(codex|agent|claude|feat|feature|fix|hotfix|wip|tmp|test|dev)(/.*)?', re.I)

ENTRY_PATTERNS = {
    'keil': ('*.uvprojx', '*.uvproj'),
    'cubemx': ('*.ioc',),
    'node': ('package.json',),
    'dotnet': ('*.sln', '*.slnx', '*.csproj'),
    'python': ('pyproject.toml', 'setup.py', 'requirements.txt'),
    'cmake': ('CMakeLists.txt',),
    'platformio': ('platformio.ini',),
    'gradle': ('build.gradle', 'build.gradle.kts'),
    'qt': ('*.pro',),
    'msvc': ('*.vcxproj',),
    'cargo': ('Cargo.toml',),
    'go': ('go.mod',),
    'iar': ('*.eww',),
}

# 类别：A 验证证据与回执；B 任务临时目录；C 历史发布包与构建日志；D 交接与进度快照
# (类别, 名称正则, 仅根目录, 把握)
DIR_RULES = [
    ('A', r'evidence|.+-evidence|\.design-evidence|\.development-evidence|validation|receipts|attempts|verification',
     False, '高'),
    ('A', r'diagnostics|screenshots', False, '复核'),
    ('B', r'\.artifacts|\.codex-artifacts|\.claude-artifacts|\.phase\d*-local|tmp|temp|\.tmp|scratch|\.scratch'
          r'|.+-work|\.release-recovery|\.project-hygiene-store', False, '高'),
    ('B', r'artifacts', True, '高'),
    ('B', r'\.local|spikes?', False, '复核'),
    ('C', r'installer-stage-.+|packages-reviewed|legacy-build|旧版|老版', False, '高'),
    ('C', r'backup|backups|bak|备份', False, '复核'),
    ('C', r'output|outputs|publish', True, '复核'),
    ('D', r'handoffs?|project-hygiene-.+', False, '高'),
    ('D', r'archive|archived|归档.*', False, '复核'),
    ('A', r'.*((19|20)\d{2}(0[1-9]|1[0-2])([0-2]\d|3[01])|(19|20)\d{2}-\d{2}-\d{2}).*', False, '复核'),
]
# (类别, 名称正则, 把握, 限定扩展名；None 表示不限)
FILE_RULES = [
    ('C', r'(build.*|.*[_-]build)\.log', '高', None),
    ('C', r'.*\.(bak|orig)|.*\.bak[_-].*|.*_deprecated.*', '高', None),
    ('C', r'.*[_-](19|20)\d{6}.*\.(hex|bin|axf|elf|exe|msi|zip|7z)', '高', None),
    ('B', r'~\$.*|.*\.tmp', '高', None),
    ('D', r'delivery-manifest.*|.*交接.*|.*handoff.*|.*进度快照.*', '复核', 'doc'),
    ('A', r'.*\.log', '复核', None),
]
DIR_RULES_C = [(c, re.compile(p, re.I), r, k) for c, p, r, k in DIR_RULES]
FILE_RULES_C = [(c, re.compile(p, re.I), k, e) for c, p, k, e in FILE_RULES]
SOURCE_ROOTS = {'src', 'source', 'sources', 'core', 'user', 'app', 'lib', 'libs', 'include', 'inc', 'sdk', 'sdks',
                'third_party', 'thirdparty', 'vendor', 'vendors', 'drivers', 'middlewares', 'packages', 'external'}
DATED_RX = DIR_RULES_C[-1][1]
REF_RE = re.compile(r'_?refs?|references?|参考.*|开发资料|例程.*', re.I)
PRODUCT_RE = re.compile(r'^[A-Za-z]{2,}(?:[-_][A-Za-z]*\d[A-Za-z0-9]*)+[-_ ]+')
DATED_EXEMPT_PARENTS = {'migrations', 'migration', 'versions', 'seeds'}
LICENSE_RE = re.compile(r'(license|licence|copying|notice)(\..*)?', re.I)
CATEGORY_NAMES = {'A': '验证证据与回执', 'B': '任务临时目录', 'C': '历史发布包与构建日志', 'D': '交接与进度快照'}

SPEC_RE = re.compile(r'协议|规格书|规格说明|契约|contract|protocol|\bprd\b|需求规格|datasheet|原理图|schematic', re.I)
PROCESS_RE = re.compile(r'核对|对比|审查|评审|排查|报告|验证|实施|记录|修改说明|接入|判读|方案|测试|validation|review'
                        r'|report|test|plan|diff', re.I)
GENERIC_PREFIX = {'修改方案', '审查报告', '评审', '评审报告', '打包记录', '接口变更', '接口契约', '接口文档', '前端对接',
                  '实施报告', '方案', '报告', '记录', '审查'}
DATE_RE = re.compile(r'[-_ ]?(19|20)\d{2}-?\d{2}-?\d{2}')
VER_RE = re.compile(r'[-_ ]?[Vv]\d+(\.\d+)*')


def lower(p: str) -> str:
    return p.lower()


def under(rel_l: str, prefix_l: str) -> bool:
    if not prefix_l:
        return True
    return rel_l == prefix_l or rel_l.startswith(prefix_l + '/')


def under_any(rel_l: str, prefixes) -> bool:
    return any(under(rel_l, p) for p in prefixes)


def human(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f}{unit}' if unit == 'B' else f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}GB'


def day(ts: float) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else ''


def is_junction(entry) -> bool:
    fn = getattr(entry, 'is_junction', None)
    try:
        return bool(fn()) if fn else False
    except OSError:
        return False


def norm_join(base: str, p: str):
    p = (p or '').replace('\\', '/').strip().strip('"')
    if not p or re.match(r'^[A-Za-z]:/', p) or p.startswith('/') or '$' in p:
        return None
    joined = posixpath.normpath(posixpath.join(base, p) if base else p)
    if joined in ('.', '') or joined.startswith('..'):
        return None
    return joined


def read_text(path: str, limit: int = 2_000_000) -> str:
    try:
        with open(path, 'rb') as f:
            data = f.read(limit)
    except OSError:
        return ''
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


def sha1(path: str) -> str:
    h = hashlib.sha1()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


# ---------------------------------------------------------------- 文件系统

class Tree:
    """一次遍历工程目录；剪掉 .git、node_modules、虚拟环境，链接与联接点不跟随。"""

    def __init__(self, root: str):
        self.root = root
        self.files: list[tuple[str, int, float]] = []
        self.dirs: list[str] = []
        self.pruned: list[tuple[str, str]] = []
        self.git_markers: list[str] = []
        self.links: list[str] = []
        self.errors: list[str] = []
        self._du: dict[str, int] = {}
        self._walk()
        self.files.sort(key=lambda f: f[0].lower())
        self._keys = [f[0].lower() for f in self.files]

    def _walk(self):
        stack = ['']
        while stack:
            rel = stack.pop()
            absd = os.path.join(self.root, rel) if rel else self.root
            try:
                entries = list(os.scandir(absd))
            except OSError as e:
                self.errors.append(f'{rel or "."}: {e.strerror}')
                continue
            names = {e.name.lower() for e in entries}
            if rel and 'pyvenv.cfg' in names:
                self.pruned.append((rel, 'venv'))
                continue
            if rel and '.git' in names:
                self.git_markers.append(rel)
            for e in entries:
                r = f'{rel}/{e.name}' if rel else e.name
                try:
                    if e.is_symlink() or is_junction(e):
                        self.links.append(r)
                        continue
                    if e.is_dir():
                        if e.name.lower() in PRUNE_NAMES:
                            if e.name.lower() != '.git':
                                self.pruned.append((r, e.name))
                            continue
                        self.dirs.append(r)
                        stack.append(r)
                    else:
                        st = e.stat()
                        self.files.append((r, st.st_size, st.st_mtime))
                except OSError as ex:
                    self.errors.append(f'{r}: {ex.strerror}')

    def files_under(self, prefix: str):
        if not prefix:
            return self.files
        p = prefix.lower() + '/'
        i = bisect.bisect_left(self._keys, p)
        j = bisect.bisect_left(self._keys, p + '\U0010ffff')
        return self.files[i:j]

    def du(self, rel: str) -> int:
        if rel in self._du:
            return self._du[rel]
        total, stack = 0, [os.path.join(self.root, rel)]
        while stack:
            d = stack.pop()
            try:
                for e in os.scandir(d):
                    try:
                        if e.is_symlink() or is_junction(e):
                            continue
                        if e.is_dir():
                            stack.append(e.path)
                        else:
                            total += e.stat().st_size
                    except OSError:
                        pass
            except OSError:
                pass
        self._du[rel] = total
        return total

    def size_under(self, prefix: str) -> tuple[int, int, float]:
        fs = self.files_under(prefix)
        size = sum(f[1] for f in fs)
        mtime = max((f[2] for f in fs), default=0)
        pl = prefix.lower()
        size += sum(self.du(r) for r, _ in self.pruned if under(r.lower(), pl))
        return size, len(fs), mtime


# ---------------------------------------------------------------- git

class Git:
    def __init__(self, root: str):
        self.root = root

    def run(self, *args: str):
        cmd = ['git', '--no-optional-locks', '-c', 'core.quotepath=false', '-c', 'core.fsmonitor=false',
               '-C', self.root, *args]
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', LC_ALL='C')
        try:
            p = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        return p.stdout.decode('utf-8', 'replace')

    def lines(self, *args: str) -> list[str]:
        out = self.run(*args)
        return [x for x in out.splitlines() if x.strip()] if out else []

    def zlist(self, *args: str) -> list[str]:
        out = self.run(*args)
        return [x for x in out.split('\0') if x] if out else []


def git_state(root: str, quick: bool) -> dict | None:
    marker = os.path.join(root, '.git')
    if not os.path.exists(marker):
        return None
    g = Git(root)
    top = (g.run('rev-parse', '--show-toplevel') or '').strip()
    st = {'kind': 'repo' if os.path.isdir(marker) else 'worktree或子模块', 'toplevel_ok':
          bool(top) and os.path.normcase(os.path.abspath(top)) == os.path.normcase(os.path.abspath(root))}
    st['branch'] = (g.run('branch', '--show-current') or '').strip()
    st['head'] = (g.run('rev-parse', '--short', 'HEAD') or '').strip()
    st['tracked'] = g.zlist('ls-files', '-z')
    st['dirty_tracked'] = len(g.zlist('status', '--porcelain=v1', '-z', '--untracked-files=no'))
    wts = [ln[9:] for ln in g.lines('worktree', 'list', '--porcelain') if ln.startswith('worktree ')]
    st['worktrees'] = [w for w in wts if os.path.normcase(os.path.abspath(w)) != os.path.normcase(os.path.abspath(root))]
    st['branches'] = g.lines('for-each-ref', '--format=%(refname:short)', 'refs/heads')
    st['tags'] = g.lines('tag', '-l')
    if quick:
        st['untracked_dirs'] = g.zlist('ls-files', '-o', '--exclude-standard', '--directory', '-z')
        st['changed'] = [x[3:] for x in g.zlist('status', '--porcelain=v1', '-z', '--untracked-files=no')
                         if len(x) > 3 and x[2] == ' ']
        return st
    st['untracked'] = g.zlist('ls-files', '-o', '--exclude-standard', '-z')
    st['stash'] = len(g.lines('stash', 'list'))
    st['remotes'] = sorted({ln.split()[0] + ' ' + ln.split()[1] for ln in g.lines('remote', '-v')
                            if len(ln.split()) >= 2})
    base = 'main' if 'main' in st['branches'] else 'master' if 'master' in st['branches'] else st['branch']
    st['base'] = base
    merged = set(g.lines('branch', '--merged', base, '--format=%(refname:short)')) if base else set()
    info = []
    for b in st['branches']:
        ahead = 0
        if base and b != base and b not in merged:
            ahead = int((g.run('rev-list', '--count', f'{base}..{b}') or '0').strip() or 0)
        info.append({'name': b, 'merged': b in merged and b != base, 'ahead_of_base': ahead})
    st['branch_info'] = info
    return st


def git_value(root: str, *args: str) -> str:
    return (Git(root).run(*args) or '').strip()


# ---------------------------------------------------------------- 工程入口与保护路径

def find_entries(rels: list[str]) -> list[tuple[str, str]]:
    found = []
    for rel in rels:
        name = posixpath.basename(rel).lower()
        for kind, pats in ENTRY_PATTERNS.items():
            if any(fnmatch.fnmatchcase(name, p.lower()) for p in pats):
                found.append((kind, rel))
    return found


def protection(root: str, entries: list[tuple[str, str]]) -> tuple[set[str], list[dict]]:
    """依据实际构建配置得出正常构建产物路径（小写相对路径）与 Keil 输出名。"""
    prot: set[str] = set()
    keil: list[dict] = []

    def add(base, p):
        j = norm_join(base, p)
        # Keil 常把输出目录设成 .\，此时它就是工程目录本身，不能把整个工程目录当构建产物保护
        if j and not under(base.lower(), j.lower()):
            prot.add(j.lower())

    for kind, rel in entries:
        d = posixpath.dirname(rel)
        absf = os.path.join(root, rel)
        if kind == 'keil':
            add(d, 'RTE')
            add(d, 'DebugConfig')
            try:
                tree = ET.fromstring(read_text(absf))
            except ET.ParseError:
                continue
            for t in tree.iter('Target'):
                outdir = t.findtext('.//OutputDirectory') or ''
                listing = t.findtext('.//ListingPath') or ''
                outname = (t.findtext('.//OutputName') or '').strip()
                add(d, outdir)
                add(d, listing)
                od = norm_join(d, outdir)
                if od:
                    keil.append({'target': (t.findtext('TargetName') or '').strip(), 'outdir': od,
                                 'outname': outname})
        elif kind == 'dotnet' and rel.lower().endswith('.csproj'):
            add(d, 'bin')
            add(d, 'obj')
            for m in re.finditer(r'<(?:Base)?OutputPath>([^<]+)<', read_text(absf)):
                add(d, m.group(1))
        elif kind == 'node':
            for x in ('dist', 'build', 'out', '.vite', 'dist-electron', 'coverage', '.next', '.nuxt'):
                add(d, x)
            try:
                pkg = json.loads(read_text(absf) or '{}')
                out = (((pkg.get('build') or {}).get('directories') or {}).get('output'))
                if isinstance(out, str):
                    add(d, out)
            except (ValueError, AttributeError):
                pass
            dabs = os.path.join(root, d) if d else root
            try:
                names = os.listdir(dabs)
            except OSError:
                names = []
            for n in names:
                nl = n.lower()
                if nl.startswith('vite.config') or nl.startswith('electron.vite.config'):
                    for m in re.finditer(r'outDir\s*:\s*[\'"]([^\'"]+)[\'"]', read_text(os.path.join(dabs, n))):
                        add(d, m.group(1))
                if nl.startswith('electron-builder') and nl.endswith(('.yml', '.yaml', '.json')):
                    for m in re.finditer(r'["\']?output["\']?\s*:\s*["\']?([^"\'\n#,}]+)',
                                         read_text(os.path.join(dabs, n))):
                        add(d, m.group(1).strip())
        elif kind == 'python':
            for x in ('.venv', 'venv', 'build', 'dist'):
                add(d, x)
        elif kind == 'cmake':
            add(d, 'build')
        elif kind == 'platformio':
            add(d, '.pio')
        elif kind == 'gradle':
            add(d, 'build')
            add(d, '.gradle')
        elif kind == 'cargo':
            add(d, 'target')
        elif kind in ('msvc', 'qt'):
            for x in ('x64', 'Debug', 'Release', 'build'):
                add(d, x)
    return prot, keil


def is_protected(rel_l: str, prot: set[str]) -> bool:
    if any(part in CACHE_NAMES for part in rel_l.split('/')):
        return True
    return under_any(rel_l, prot)


def match_dir(rel: str, skip_dated: bool = False):
    name = posixpath.basename(rel)
    parents = rel.lower().split('/')[:-1]
    for cat, rx, root_only, conf in (DIR_RULES_C[:-1] if skip_dated else DIR_RULES_C):
        if root_only and parents:
            continue
        if not rx.fullmatch(name):
            continue
        if conf == '复核' and any(p in SOURCE_ROOTS for p in parents):
            continue  # 源码树里的 Diagnostics/、Archive/ 是代码，不是过程文件
        if cat == 'D' and conf == '复核' and any(p in SPEC_DIR_NAMES for p in parents):
            continue
        if parents and parents[-1] in DATED_EXEMPT_PARENTS:
            continue  # 数据库迁移等按日期命名的源码目录
        return cat, conf
    return None


def match_file(rel: str):
    name = posixpath.basename(rel)
    parents = rel.lower().split('/')[:-1]
    ext = posixpath.splitext(name)[1].lower()
    for cat, rx, conf, only in FILE_RULES_C:
        if not rx.fullmatch(name):
            continue
        if only == 'doc' and ext not in DOC_EXT | {'.json'}:
            continue
        if cat == 'D' and any(p in SPEC_DIR_NAMES for p in parents):
            continue
        return cat, conf
    return None


# ---------------------------------------------------------------- 文档

def topic_key(stem: str) -> str:
    s = re.sub(r'^\d+[.\-_ ]+', '', stem)
    s = re.sub(r'[（(]\d+[)）]', '', s)
    s = DATE_RE.sub('', s)
    s = VER_RE.sub('', s)
    s = PRODUCT_RE.sub('', s, count=1) or s  # 去掉 ABC-1234A- 这类型号前缀
    parts = [p.strip() for p in re.split(r'[-_—–]', s) if p.strip()]
    if not parts:
        return stem
    if parts[0] in GENERIC_PREFIX and len(parts) > 1:
        return parts[0] + '·' + parts[1]
    return parts[0]


def is_spec_name(name: str) -> bool:
    return bool(SPEC_RE.search(name)) and not PROCESS_RE.search(name)


def spec_identity(stem: str):
    s = re.sub(r'^\d+[.\-_ ]+', '', stem)
    ver = re.search(r'[Vv](\d+(?:\.\d+)+)', s)
    s = DATE_RE.sub('', VER_RE.sub('', s))
    token = next((p for p in re.split(r'[-_ ]+', s) if SPEC_RE.search(p)), s)
    return token.strip(), (ver.group(1) if ver else '')


# ---------------------------------------------------------------- VERSION 与 release

def parse_version_file(path: str):
    data, bad = {}, []
    for i, line in enumerate(read_text(path).splitlines(), 1):
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if '=' not in s:
            bad.append(f'第 {i} 行不是 key = value')
            continue
        k, v = s.split('=', 1)
        data[k.strip().lower()] = v.strip()
    return data, bad


def protocol_segment(protocol: str):
    p = (protocol or '').strip()
    if p in ('', '-', '无', 'none'):
        return 0
    m = re.fullmatch(r'[Vv]?(\d+)\.(\d+)', p)
    return int(m.group(1)) * 100 + int(m.group(2)) if m else None


def project_type(root: str) -> str:
    """VERSION 里的 type；只有 docs（文档/素材型工程）一种取值，代码工程不写。"""
    path = os.path.join(root, 'VERSION')
    return parse_version_file(path)[0].get('type', '').lower() if os.path.isfile(path) else ''


def check_version(root: str, git: dict | None) -> dict:
    path = os.path.join(root, 'VERSION')
    if not os.path.isfile(path):
        return {'exists': False, 'issues': ['缺少根目录 VERSION 文件（未接入版本规范）']}
    data, issues = parse_version_file(path)
    typ = data.get('type', '').lower()
    if typ == 'docs':
        if data.get('layout', '') != LAYOUT_VERSION:
            issues.append(f'文档型工程需声明 layout = {LAYOUT_VERSION}')
        extra = [k for k in ('product', 'version', 'hardware', 'protocol') if k in data]
        if extra:
            issues.append('文档型工程不填版本三段，删去：' + '、'.join(extra))
        return {'exists': True, 'data': data, 'issues': issues}
    if typ:
        issues.append(f'type「{typ}」无法识别（只能是 docs，代码工程不写 type）')
    for k in ('product', 'version', 'hardware', 'protocol'):
        if k not in data:
            issues.append(f'缺少字段 {k}')
    ver = data.get('version', '')
    m = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', ver)
    if not m:
        issues.append(f'version「{ver}」不是三段数字')
    else:
        h, p, _ = (int(x) for x in m.groups())
        if data.get('hardware', '').isdigit() and int(data['hardware']) != h:
            issues.append(f'hardware={data["hardware"]} 与版本号硬件段 {h} 不一致')
        seg = protocol_segment(data.get('protocol', ''))
        if seg is None:
            issues.append(f'protocol「{data.get("protocol")}」无法解析（应为 V主.次）')
        elif seg != p:
            issues.append(f'协议段应为 {seg}（{data.get("protocol")} → 主×100+次），实际为 {p}')
    doc = data.get('protocol_doc', '')
    if protocol_segment(data.get('protocol', '')) not in (0, None):
        if not doc:
            issues.append('缺少 protocol_doc（当前采用的协议文档路径）')
        elif not os.path.isfile(os.path.join(root, doc)):
            issues.append(f'protocol_doc 指向的文件不存在：{doc}')
    for vf in [x.strip() for x in data.get('version_files', '').split(',') if x.strip()]:
        fp = os.path.join(root, vf)
        if not os.path.isfile(fp):
            issues.append(f'version_files 中的文件不存在：{vf}')
        elif ver and ver not in read_text(fp):
            issues.append(f'{vf} 中未找到版本号 {ver}')
    lay = data.get('layout', '')
    if lay and lay != LAYOUT_VERSION:
        issues.append(f'layout「{lay}」无法识别（当前布局规范版本为 {LAYOUT_VERSION}）')
    variant = data.get('variant', '')
    if git:
        br = git.get('branch', '')
        mm = re.fullmatch(r'protocol/v(\d+\.\d+)((?:-[a-z0-9]+)*)', br)
        br_variant = mm.group(2)[1:] if mm else ''
        if br_variant != variant:
            issues.append(f'variant「{variant}」与当前分支「{br}」不一致')
        if mm and data.get('protocol', '').lstrip('Vv') != mm.group(1):
            issues.append(f'协议线分支 {br} 与 VERSION 中 protocol={data.get("protocol")} 不一致')
        tag = f'v{ver}' + (f'-{variant}' if variant else '')
        if ver and tag in git.get('tags', []):
            tag_commit = git_value(root, 'rev-list', '-n', '1', tag)
            head = git_value(root, 'rev-parse', 'HEAD')
            if tag_commit and head and tag_commit != head:
                issues.append(f'版本 {tag} 已打 tag 并冻结，之后又有新提交，固件段需要 +1')
    return {'exists': True, 'data': data, 'issues': issues}


def check_release(root: str, vinfo: dict, tracked_l: set[str], git: dict | None) -> list[str]:
    rdir = os.path.join(root, 'release')
    if not os.path.isdir(rdir):
        return []
    issues = []
    data = vinfo.get('data') or {}
    product, ver, variant = data.get('product', ''), data.get('version', ''), data.get('variant', '')
    rx = None
    if product and ver:
        rx = re.compile(re.escape(product) + r'(-[A-Za-z0-9]+)?_V' + re.escape(ver)
                        + (r'_' + re.escape(variant) if variant else '') + r'\.[A-Za-z0-9]+')
    subdirs, files = [], []
    for e in sorted(os.scandir(rdir), key=lambda x: x.name):
        (subdirs if e.is_dir() else files).append(e.name)
    if subdirs:
        issues.append(f'release/ 下有 {len(subdirs)} 个子目录（只应放当前交付物文件）：'
                      + '、'.join(subdirs[:5]) + ('…' if len(subdirs) > 5 else ''))
    if files and not rx:
        issues.append(f'release/ 有 {len(files)} 个文件，工程未接入 VERSION，无法校验交付物版本')
    elif rx:
        for name in files:
            if not rx.fullmatch(name):
                issues.append(f'release/{name}：文件名与 VERSION 不符（期望 {product}[-组件]_V{ver}'
                              f'{"_" + variant if variant else ""}.扩展名）')
    if git:
        tracked = [x for x in tracked_l if x.startswith('release/')]
        if tracked:
            issues.append(f'release/ 中 {len(tracked)} 个文件被提交进了 git（交付物不入库）')
        if not git_value(root, 'check-ignore', 'release/x'):
            issues.append('release/ 未加入 .gitignore')
    return issues


# ---------------------------------------------------------------- 目录布局（layout v1，规则见 references/目录布局.md）

LAYOUT_VERSION = '1'
# 顶层目录词表（单一来源；references/目录布局.md 的词表由 tests/test_scan.py 校验与此一致）
LAYOUT_V1 = {
    'firmware': '运行在 MCU 上的程序，一块板一个子目录 firmware/<板目录>/',
    'host': '用户在 PC 上运行的程序（GUI、CLI、桌面应用）',
    'server': '后端服务',
    'web': '浏览器前端',
    'app': '移动端、TV 大屏等终端 App',
    'deploy': '部署：compose、Dockerfile、nginx、安装包工程、现场启停脚本',
    'contracts': '机器可读接口契约（OpenAPI、JSON Schema、proto）',
    'tools': '开发辅助，不交付',
    'tests': '测试，多组件时 tests/<组件>/',
    'docs': '文档十类',
    'release': '当前交付物（gitignore）',
}
# 文档/素材型工程（VERSION 声明 type = docs）的顶层词表；references/文档整理.md 的词表由 tests/test_scan.py 校验一致
DOCS_LAYOUT_V1 = {
    'docs': '文档十类',
    'assets': '素材，按阶段分 参考/ 原图/ 定稿/',
    'tools': '生成或处理素材的脚本',
    'release': '对外发出的打包件，只留最近一次',
}
ASSET_STAGES = ('参考', '原图', '定稿')
ECOSYSTEM_DIRS = {'apps', 'packages', 'src'}  # 仅工作区型工程
COMPONENT_WORDS = ('firmware', 'host', 'server', 'web', 'app')
ROOT_CONFIG_DIRS = {'.git', '.github', '.gitea', '.vscode', '.claude', '.idea', '.husky', '.changeset', '.devcontainer',
                    '.kiro', '.cursor', '.windsurf', '.trae'}
ROOT_ENV_DIRS = {'node_modules', '.venv', 'venv', 'env', '.turbo', '.pnpm-store', '.nx', '.cache'} | CACHE_NAMES
ROOT_FILE_OK = re.compile(r'(readme.*|claude\.md|agents\.md|version|license.*|licence.*|changelog.*|contributing.*'
                          r'|\.gitignore|\.gitattributes|\.gitmodules|\.editorconfig)', re.I)
WORKSPACE_FILE_OK = re.compile(
    r'(package\.json|pnpm-workspace\.yaml|pnpm-lock\.yaml|package-lock\.json|yarn\.lock|turbo\.json|nx\.json'
    r'|lerna\.json|tsconfig(\.[\w-]+)?\.json|(eslint|prettier|commitlint|lint-staged|vitest\.workspace)\.config\.'
    r'[cm]?[jt]s|vitest\.workspace\.[cm]?[jt]s|\.eslintrc.*|\.prettierrc.*|\.prettierignore|\.eslintignore|\.npmrc'
    r'|\.nvmrc|\.node-version|.*\.slnx?|directory\.(build|packages)\.(props|targets)|global\.json|nuget\.config'
    r'|\.dockerignore|\.env\.example|requirements.*\.txt|.*\.lock(\.txt)?)', re.I)
SCRIPT_EXT = {'.bat', '.cmd', '.ps1', '.sh', '.py', '.vbs'}
MEANINGLESS_RE = re.compile(r'(^|[-_ ])(test|tets|tset|new|copy|demo|old|tmp|temp)\d*($|[-_ ])|副本|新建', re.I)
CUBEMX_OWNED = {'core', 'drivers', 'middlewares', 'mdk-arm', 'ewarm', 'stm32cubeide', 'fatfs', 'usb_host',
                'usb_device', 'lwip', 'libjpeg', 'mbedtls', 'pdm2pcm', 'touchsensing', 'x-cube-ai', 'cmake'}
STDLIB_OWNED = {'drivers', 'mdk-arm'}
STDLIB_LIB_RE = re.compile(r'librar(y|ies)|libs?|fwlib|std_?periph.*|cmsis', re.I)
USER_LAYERS = {'app', 'bsp', 'thirdparty'}
UNKNOWN_DIR_HINTS = [
    (r'scripts?', '合并：组件自己的启动/打包脚本归组件，其余进 tools/'),
    (r'installers?', '安装包工程属于部署 → deploy/installer/'),
    (r'sdks?|vendors?|third_?party|external', '构建引用的 → <组件>/ThirdParty/；仅供参考的 → 参考库'),
    (r'参考.*|开发资料|资料|datasheets?', '器件规格书 → docs/参考资料/；大件厂家资料包 → 参考库'),
    (r'data|uploads?|models?|logs?|storage', '运行时数据：归入所属组件内（如 server/data/）并加入 .gitignore'),
    (r'dist|build|out|output|outputs|publish|bin|obj', '构建输出：放组件内并 gitignore；交付物进 release/'),
    (r'src|lib|public|static|templates|assets', '应用源码/资源：随所属组件移入 host/ web/ server/'),
    (r'.*app|mobile.*|tv.*|android|ios|miniprogram', '终端 App → app/<名>/'),
    (r'frontend|client|ui', '前端 → web/'),
    (r'backend|api|service', '后端 → server/'),
    (r'compose|docker|nginx|k8s|helm|ansible', '部署配置 → deploy/'),
    (r'test|testing|e2e|spec|fixtures', '测试 → tests/<组件>/'),
    (r'doc|documents?|文档|说明', '文档 → docs/ 十类'),
    (r'contract|schemas?|proto|openapi', '机器可读契约 → contracts/；纯文字接口说明 → docs/接口/'),
]
UNKNOWN_DIR_HINTS_C = [(re.compile(p, re.I), h) for p, h in UNKNOWN_DIR_HINTS]
GUI_IMPORT_RE = re.compile(r'^\s*(?:import|from)\s+(tkinter|PyQt5|PyQt6|PySide2|PySide6|wx|customtkinter|dearpygui'
                           r'|kivy|flet|ttkbootstrap)\b', re.M)
SERVER_IMPORT_RE = re.compile(r'^\s*(?:import|from)\s+(flask|fastapi|django|aiohttp|tornado|sanic|uvicorn|bottle)\b',
                              re.M)
LOCAL_IMPORT_RE = re.compile(r'^\s*(?:import\s+([A-Za-z_]\w*)|from\s+([A-Za-z_]\w*)\s+import)', re.M)
NOT_COMPONENT_DIRS = {'tests', 'test', 'docs', 'deploy', 'release'}
NOT_COMPONENT_SEGS = {'tools', 'tests', 'test', 'samples', 'sample', 'examples', 'example', 'demos', 'demo'}
KIND_RANK = {'server': 3, 'web': 2, 'host': 2, 'app': 2, '': 0}


def workspace_kind(root: str, names_l: set[str]) -> str:
    """根目录是生态工作区（pnpm/turbo/nx/lerna 或 .sln）时返回其类型，否则空串。"""
    if names_l & {'pnpm-workspace.yaml', 'turbo.json', 'nx.json', 'lerna.json'}:
        return 'node'
    if 'package.json' in names_l:
        try:
            if 'workspaces' in json.loads(read_text(os.path.join(root, 'package.json')) or '{}'):
                return 'node'
        except ValueError:
            pass
    if any(n.endswith(('.sln', '.slnx')) for n in names_l):
        return 'dotnet'
    return ''


def keil_refs(root: str, rel: str) -> list[str]:
    """uvprojx/uvproj 中 FilePath 与 IncludePath 指向的工程内相对路径（小写不变）。"""
    d = posixpath.dirname(rel)
    try:
        t = ET.fromstring(read_text(os.path.join(root, rel)))
    except ET.ParseError:
        return []
    out = []
    for e in t.iter('FilePath'):
        j = norm_join(d, e.text or '')
        if j:
            out.append(posixpath.dirname(j))
    for e in t.iter('IncludePath'):
        for p in (e.text or '').split(';'):
            j = norm_join(d, p)
            if j:
                out.append(j)
    return out


def common_dir(paths: list[str]) -> str:
    parts = [p.split('/') for p in paths if p is not None]
    if not parts:
        return ''
    out = []
    for segs in zip(*parts):
        if len({s.lower() for s in segs}) != 1:
            break
        out.append(segs[0])
    return '/'.join(out)


def ioc_value(path: str, key: str) -> str:
    m = re.search(r'^' + re.escape(key) + r'=(.*)$', read_text(path), re.M)
    return m.group(1).strip() if m else ''


def node_kind(root: str, rel: str) -> str:
    d = posixpath.dirname(rel)
    try:
        pkg = json.loads(read_text(os.path.join(root, rel)) or '{}')
    except ValueError:
        pkg = {}
    deps = {}
    for k in ('dependencies', 'devDependencies'):
        if isinstance(pkg.get(k), dict):
            deps.update(pkg[k])
    if os.path.isdir(os.path.join(root, d, 'src-tauri')) or '@tauri-apps/api' in deps or 'electron' in deps:
        return 'host'
    if 'react-native' in deps or 'expo' in deps or '@dcloudio/uni-app' in deps:
        return 'app'
    if any(k in deps for k in ('express', 'koa', 'fastify', '@nestjs/core', '@hapi/hapi', 'hono')):
        return 'server'
    if any(k in deps for k in ('react', 'vue', 'vite', 'svelte', '@angular/core', 'next', 'nuxt')):
        return 'web'
    return ''


def dotnet_kind(root: str, rel: str) -> str:
    t = read_text(os.path.join(root, rel))
    if 'Microsoft.NET.Sdk.Web' in t:
        return 'server'
    if re.search(r'<Use(WPF|WindowsForms)>\s*true', t, re.I) or 'Sdk.WindowsDesktop' in t:
        return 'host'
    return ''


def python_kind(texts: list[str]) -> str:
    if any(GUI_IMPORT_RE.search(t) for t in texts):
        return 'host'
    if any(SERVER_IMPORT_RE.search(t) for t in texts):
        return 'server'
    return ''


def first_seg(rel: str) -> str:
    return rel.split('/', 1)[0]


def skip_for_components(rl: str, prot: set[str], claimed: list[str], ws: str, allow_tools: bool = False) -> bool:
    parts = rl.split('/')
    if under_any(rl, claimed) or is_protected(rl, prot):
        return True
    if any(p in VENDOR_NAMES for p in parts[:-1]):
        return True
    if len(parts) > 1 and parts[0] in NOT_COMPONENT_DIRS | (set() if allow_tools else {'tools'}):
        return True  # 测试工程、开发辅助、部署工程不是要搬的组件
    if ws and len(parts) > 1 and parts[0] in ECOSYSTEM_DIRS:
        return True
    return False


def detect_components(root: str, tree: Tree, entries: list[tuple[str, str]], prot: set[str],
                      claimed: list[str], ws: str) -> list[dict]:
    comps: list[dict] = []

    def owner(rel_dir: str):
        for c in comps:
            if c['type'].startswith('firmware') and c['root'].lower() == rel_dir.lower():
                return c
        return None

    # 固件：先 CubeMX（以 .ioc 所在目录为组件根），再 Keil（以引用路径的公共目录为组件根），IAR 只做归并
    for kind, rel in sorted(entries, key=lambda x: ({'cubemx': 0, 'keil': 1, 'iar': 2}.get(x[0], 9), x[1])):
        rl = rel.lower()
        if kind not in ('cubemx', 'keil', 'iar') or skip_for_components(rl, prot, claimed, ws):
            continue
        d = posixpath.dirname(rel)
        if kind == 'cubemx':
            if owner(d):
                continue
            comps.append({'type': 'firmware-cubemx', 'root': d, 'entry': rel,
                          'name': posixpath.splitext(posixpath.basename(rel))[0], 'keil': [], 'refs': []})
        elif kind == 'keil':
            refs = keil_refs(root, rel)
            croot = common_dir(refs + [d]) if refs else posixpath.dirname(d)
            parent = posixpath.dirname(d)
            c = owner(parent) or owner(croot)
            if c:
                c['keil'].append(rel)
                c['refs'] += refs
                continue
            comps.append({'type': 'firmware-keil', 'root': croot, 'entry': rel,
                          'name': posixpath.splitext(posixpath.basename(rel))[0], 'keil': [rel], 'refs': refs})
        else:
            parent = posixpath.dirname(d) if posixpath.basename(d).lower() == 'ewarm' else d
            if not owner(parent):
                comps.append({'type': 'firmware-iar', 'root': parent, 'entry': rel,
                              'name': posixpath.splitext(posixpath.basename(rel))[0], 'keil': [], 'refs': []})
    fw_roots = [c['root'].lower() for c in comps]

    def inside_fw(rl: str) -> bool:
        return any(under(rl, r) for r in fw_roots if r)

    # Node / .NET / Android 单应用：浅层优先，.sln 先于 .csproj；子工程并入父组件，父组件沿用子工程识别出的类型
    def depth_key(x):
        sln_first = 0 if x[1].lower().endswith(('.sln', '.slnx')) else 1
        return (x[1].count('/'), sln_first, x[1])
    for kind, rel in sorted(entries, key=depth_key):
        rl = rel.lower()
        if kind not in ('node', 'dotnet', 'gradle') or skip_for_components(rl, prot, claimed, ws) or inside_fw(rl):
            continue
        if any(p in NOT_COMPONENT_SEGS for p in rl.split('/')[:-1]):
            continue  # 解决方案里的测试工程、探针工具、厂商样例
        d = posixpath.dirname(rel)
        if ws and d == '':
            continue  # 工作区根本身
        if kind == 'node':
            k = node_kind(root, rel)
        elif kind == 'dotnet':
            k = '' if rl.endswith(('.sln', '.slnx')) else dotnet_kind(root, rel)
        else:
            k = 'app' if 'com.android.application' in read_text(os.path.join(root, rel)) else ''
        parent = next((c for c in comps if not c['type'].startswith('firmware')
                       and (c['root'].lower() == d.lower() or (c['root'] and under(d.lower(), c['root'].lower()))
                            or (c['root'] == '' and not ws))), None)
        if parent:
            if parent['type'].endswith('-' + kind) and KIND_RANK.get(k, 0) > KIND_RANK.get(parent['kind'], 0):
                parent['kind'], parent['type'] = k, f'{k}-{kind}'
            continue
        comps.append({'type': f'{k or "unknown"}-{kind}', 'kind': k, 'root': d, 'entry': rel,
                      'name': posixpath.basename(d) or os.path.basename(root)})

    # Python：带依赖清单（requirements/pyproject/setup.py）的目录连同子目录整体算一个组件；
    # 其余按 GUI/服务端入口文件及其同目录本地 import 闭包算文件级组件
    taken = [c['root'].lower() for c in comps]
    py_entries = sorted({posixpath.dirname(r) for k, r in entries if k == 'python'
                         and not skip_for_components(r.lower(), prot, claimed, ws, allow_tools=True)},
                        key=lambda x: -len(x))
    py_by_dir: dict[str, list[str]] = {}
    for rel, size, _ in tree.files:
        rl = rel.lower()
        if not rl.endswith('.py') or skip_for_components(rl, prot, claimed, ws, allow_tools=True) or inside_fw(rl):
            continue
        if any(t and under(rl, t) for t in taken):
            continue
        d = posixpath.dirname(rel)
        owner_dir = next((e for e in py_entries if under(d.lower(), e.lower())
                          and not (e == '' and d and first_seg(d).lower() in LAYOUT_V1)), None)
        py_by_dir.setdefault(d if owner_dir is None else owner_dir, []).append(rel)
    py_entries_l = {e.lower() for e in py_entries}
    for d, files in sorted(py_by_dir.items()):
        if len(files) > 400:
            files = files[:400]
        texts = {f: read_text(os.path.join(root, f), 65536) for f in files}
        local = {posixpath.splitext(posixpath.basename(f))[0]: f for f in files}
        seeds = [f for f in files if GUI_IMPORT_RE.search(texts[f])] or \
                [f for f in files if SERVER_IMPORT_RE.search(texts[f])]
        if not seeds:
            continue
        k = python_kind([texts[f] for f in seeds])
        closure, todo = set(), list(seeds)
        while todo:
            f = todo.pop()
            if f in closure:
                continue
            closure.add(f)
            for m in LOCAL_IMPORT_RE.finditer(texts[f]):
                dep = local.get(m.group(1) or m.group(2))
                if dep and dep not in closure:
                    todo.append(dep)
        whole = d.lower() in py_entries_l or (len(closure) == len(files) and first_seg(d).lower() not in LAYOUT_V1)
        if whole:
            name = posixpath.basename(d) or os.path.basename(root)
        else:
            name = posixpath.splitext(posixpath.basename(sorted(seeds)[0]))[0]
        comps.append({'type': f'{k}-python', 'kind': k, 'root': d, 'entry': sorted(seeds)[0], 'name': name,
                      'files': [] if whole else sorted(closure),
                      'review': first_seg(d).lower() == 'tools'})  # tools/ 里的可能是开发者自用工具，需人工复核
    for c in comps:
        c.setdefault('kind', 'firmware' if c['type'].startswith('firmware') else '')
    return comps


def place_components(root: str, comps: list[dict], top_names: list[str], prot: set[str]) -> list[dict]:
    """给每个组件算期望位置与迁移建议（from → to）。根目录型组件按引用/归属拆成逐项迁移；
    构建产物（保护路径）不列入，换位置后重新构建即可。"""
    moves = []
    by_kind: dict[str, int] = {}
    for c in comps:
        if c['kind']:
            by_kind[c['kind']] = by_kind.get(c['kind'], 0) + 1
    assigned: set[str] = set()
    for c in comps:
        k, r = c['kind'], c['root']
        if not k:
            c['ok'], c['expected'] = None, ''
            continue
        if k == 'firmware':
            c['expected'] = f'firmware/{c["name"]}'
            c['ok'] = posixpath.dirname(r).lower() == 'firmware'
        else:
            c['expected'] = k if by_kind[k] == 1 else f'{k}/{c["name"]}'
            c['ok'] = r.lower() == k or posixpath.dirname(r).lower() == k
        if c['ok']:
            continue
        why = f'{c["type"]}（入口 {c["entry"]}）' + (
            '；位于 tools/，交付给用户的上位机才迁移，开发者自用工具保留 tools/' if c.get('review') else '')
        if c.get('files'):
            for f in c['files']:
                moves.append({'from': f, 'to': f'{c["expected"]}/{posixpath.basename(f)}', 'why': why})
        elif r:
            moves.append({'from': r + '/', 'to': c['expected'] + '/', 'why': why})
        else:
            # 组件铺在工程根：固件按构建引用 + CubeMX 自有目录归属，其余组件认领词表外的根目录项
            if k == 'firmware':
                own = {first_seg(p).lower() for p in c['refs']} | CUBEMX_OWNED | STDLIB_OWNED | {'user'}
                items = [n for n in top_names if n.lower() in own or n.lower().endswith(('.ioc', '.mxproject'))
                         or n.lower() == '.mxproject']
            else:
                items = [n for n in top_names if n.lower() not in LAYOUT_V1 and n.lower() not in ROOT_CONFIG_DIRS
                         and n.lower() not in ROOT_ENV_DIRS and not ROOT_FILE_OK.fullmatch(n)
                         and not is_root_doc(n)]
            for n in items:
                if n.lower() in assigned or n.lower() in LAYOUT_V1 or is_protected(n.lower(), prot):
                    continue
                assigned.add(n.lower())
                isdir = os.path.isdir(os.path.join(root, n))
                moves.append({'from': n + ('/' if isdir else ''), 'to': f'{c["expected"]}/{n}' + ('/' if isdir else ''),
                              'why': why})
    return moves


def firmware_internal(root: str, tree: Tree, c: dict, top_names: list[str]) -> dict:
    """固件组件内部：Core/ 下的自写文件、CubeMX 目录内的多余项、User/ 未分层。只给建议。"""
    r = c['root']
    base = os.path.join(root, r) if r else root
    if r:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            names = []
    else:
        own = {first_seg(p).lower() for p in c['refs']} | CUBEMX_OWNED | STDLIB_OWNED | {'user'}
        names = [n for n in top_names if n.lower() in own]
    out = {'component': r or '.', 'core_user_files': [], 'extras': [], 'user_unlayered': []}
    pre = (r + '/') if r else ''
    if c['type'] == 'firmware-cubemx':
        for rel, size, _ in tree.files_under(pre + 'Core'):
            if posixpath.splitext(rel)[1].lower() not in ('.c', '.h', '.cpp', '.hpp'):
                continue
            t = read_text(os.path.join(root, rel), 262144)
            if 'USER CODE BEGIN' not in t and 'STMicroelectronics' not in t:
                out['core_user_files'].append(rel)
    refs_top = {first_seg(p[len(pre):]).lower() for p in c['refs'] if not pre or p.lower().startswith(pre.lower())}
    owned = CUBEMX_OWNED if c['type'] == 'firmware-cubemx' else STDLIB_OWNED | CUBEMX_OWNED  # 无 ioc 但 CubeMX 形态
    if r:
        for n in names:
            nl = n.lower()
            if nl in owned or nl == 'user' or nl.endswith(('.ioc', '.mxproject')) or nl.startswith('readme') \
                    or nl in ('.gitignore', '.extsettings', '.settings'):
                continue
            ext = posixpath.splitext(nl)[1]
            if ext in SCRIPT_EXT:
                hint = 'CubeMX/Keil 目录内不放脚本 → tools/（如 keilkill.bat → tools/clean_keil.bat）'
            elif c['type'] != 'firmware-cubemx' and STDLIB_LIB_RE.fullmatch(nl):
                hint = '厂商库与 CMSIS → Drivers/（同步改 uvprojx 路径）'
            elif nl in refs_top:
                hint = '被 uvprojx 引用的自写或第三方代码 → User/App、User/BSP 或 User/ThirdParty/'
            else:
                hint = '固件目录内只允许生成目录、README.md 与 User/'
            out['extras'].append({'path': pre + n, 'hint': hint})
    elif c['type'] != 'firmware-cubemx':
        for n in names:
            if STDLIB_LIB_RE.fullmatch(n.lower()):
                out['extras'].append({'path': n, 'hint': '厂商库与 CMSIS → Drivers/（同步改 uvprojx 路径）'})
            elif n.lower() in refs_top and n.lower() not in owned and n.lower() != 'user':
                out['extras'].append({'path': n, 'hint': '被 uvprojx 引用的第三方或自写代码 → User/ThirdParty/ 或 User/App/'})
    udir = next((n for n in names if n.lower() == 'user'), None)
    if udir:
        try:
            kids = sorted(os.listdir(os.path.join(base, udir)))
        except OSError:
            kids = []
        out['user_unlayered'] = [f'{pre}{udir}/{k}' for k in kids if k.lower() not in USER_LAYERS
                                 and not k.lower().startswith('readme')]
    return out


def ioc_issues(root: str, c: dict) -> list[str]:
    out = []
    r = c['root']
    if c['type'] == 'firmware-cubemx':
        ioc = os.path.join(root, c['entry'])
        stem = c['name']
        pname = ioc_value(ioc, 'ProjectManager.ProjectName')
        if r and posixpath.basename(r) != stem:
            out.append(f'{r}/：目录名与 ioc 名「{stem}」不一致（约定目录名 = ioc 名；迁入 firmware/ 时改用 ioc 名）')
        if pname and pname != stem:
            out.append(f'{c["entry"]}：ProjectName「{pname}」与 ioc 文件名不一致')
        uvs = [k for k in c['keil'] if posixpath.basename(posixpath.dirname(k)).lower() == 'mdk-arm']
        for uv in uvs:
            ustem = posixpath.splitext(posixpath.basename(uv))[0]
            if pname and ustem != pname:
                out.append(f'{uv}：硬约束——uvprojx 文件名「{ustem}」≠ ProjectName「{pname}」，CubeMX 重新生成会另起一个'
                           f'新 uvprojx 并丢失用户分组')
        if MEANINGLESS_RE.search(stem):
            out.append(f'「{stem}」看不出含义：建议出改名方案（ioc、ProjectName、uvprojx/uvoptx、Target/Output 联动）')
    elif c['type'] == 'firmware-keil' and MEANINGLESS_RE.search(c['name']):
        out.append(f'「{c["name"]}」看不出含义：建议出改名方案（uvprojx/uvoptx、Target/Output 联动）')
    return out


def readme_table(root: str):
    """根 README「目录结构」表第一列里的路径；没有该节返回 None。"""
    name = next((n for n in os.listdir(root) if n.lower() == 'readme.md'), None)
    if not name:
        return None
    text = read_text(os.path.join(root, name))
    m = re.search(r'^#{2,3}\s*目录结构\s*$', text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    n = re.search(r'^#{1,3}\s', rest, re.M)
    section = rest[:n.start()] if n else rest
    paths = []
    for line in section.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        first = s.strip('|').split('|')[0].strip()
        if re.fullmatch(r':?-+:?', first) or '分支' in first or 'branch' in first.lower():
            continue
        for code in re.findall(r'`([^`]+)`', first):
            p = re.sub(r'^\./', '', code.strip().replace('\\', '/')).strip('/')
            if p and not re.match(r'^[A-Za-z]:', p) and '<' not in p and '*' not in p:
                paths.append(p)
    return paths


def root_entries(root: str) -> list[tuple[str, bool]]:
    try:
        return sorted(((e.name, e.is_dir()) for e in os.scandir(root)), key=lambda x: x[0].lower())
    except OSError:
        return []


def root_checks(root: str, ents: list[tuple[str, bool]], ws: str, skip: set[str], tracked_top: set[str] | None):
    """根目录白名单（文件）与词表（目录）。skip 为已由组件迁移覆盖的项（小写）。"""
    viol, unknown = [], []
    for n, isdir in ents:
        nl = n.lower()
        if nl in skip:
            continue
        if isdir:
            if nl in LAYOUT_V1 or nl in ROOT_CONFIG_DIRS or nl in ROOT_ENV_DIRS or (ws and nl in ECOSYSTEM_DIRS):
                continue
            hint = next((h for rx, h in UNKNOWN_DIR_HINTS_C if rx.fullmatch(nl)),
                        '词表外目录：归入已有词，归不进再向用户申请新增词')
            unknown.append({'path': n + '/', 'hint': hint,
                            'tracked': None if tracked_top is None else nl in tracked_top})
            continue
        if ROOT_FILE_OK.fullmatch(n) or (ws and WORKSPACE_FILE_OK.fullmatch(n)) or is_root_doc(n):
            continue  # 散放文档由 docs.root_docs 报告
        ext = posixpath.splitext(nl)[1]
        if ext in SCRIPT_EXT:
            hint = '根目录不放脚本：组件的启动/打包脚本归组件，其余进 tools/；脚本名用 ASCII「动词_对象」'
            if CJK_RE.search(n) or not n.isascii():
                hint += '（当前文件名含非 ASCII 字符）'
        elif re.fullmatch(r'(docker-)?compose.*\.ya?ml|dockerfile.*|nginx.*\.conf|ecosystem\.config\.[cm]?js', nl):
            hint = '部署配置 → deploy/（同步改 build context、volume 等相对路径）'
        elif WORKSPACE_FILE_OK.fullmatch(n):
            hint = '应用的构建配置：随所属组件移入 host/ web/ server/'
        else:
            hint = '根目录只放说明与配置：归入所属组件、docs/ 或 deploy/'
        viol.append({'path': n, 'hint': hint})
    return viol, unknown


def script_targets(root: str, viol: list[dict], comps: list[dict]):
    """根目录脚本里引用了哪个组件的文件，就建议跟着那个组件走。"""
    for v in viol:
        if posixpath.splitext(v['path'])[1].lower() not in SCRIPT_EXT:
            continue
        t = read_text(os.path.join(root, v['path']), 65536).replace('\\', '/').lower()
        for c in comps:
            keys = [f.lower() for f in c.get('files') or []] or ([c['root'].lower() + '/'] if c['root'] else [])
            if c['kind'] and any(k and k in t for k in keys):
                v['to'] = c['expected'] + '/'
                v['hint'] = f'引用了 {c["type"]} 组件 → {c["expected"]}/' + (
                    '（改为 ASCII 名，如 run_xxx.bat）' if not v['path'].isascii() else '')
                break


def scan_layout(root: str, tree: Tree, entries: list[tuple[str, str]], prot: set[str], claimed: list[str],
                vinfo: dict, tracked_l: set[str], git: dict | None, phase_a_root: set[str]) -> dict:
    ents = root_entries(root)
    names_l = {n.lower() for n, _ in ents}
    ws = workspace_kind(root, names_l)
    declared = 1 if (vinfo.get('data') or {}).get('layout', '') == LAYOUT_VERSION else None
    top_names = [n for n, _ in ents if n.lower() not in ROOT_CONFIG_DIRS]
    comps = detect_components(root, tree, entries, prot, claimed, ws)
    moves = place_components(root, comps, top_names, prot)
    skip = {m['from'].rstrip('/').lower() for m in moves if '/' not in m['from'].rstrip('/')}
    skip |= {c.rstrip('/') for c in claimed if '/' not in c.rstrip('/')}  # 阶段 A 已认领的（如 _ref/、backup/）
    skip |= set(phase_a_root)  # 阶段 A 已列出的根目录文件不重复报
    tracked_top = {first_seg(t) for t in tracked_l} if git else None
    viol, unknown = root_checks(root, ents, ws, skip, tracked_top)
    script_targets(root, viol, comps)
    internal, ioc = [], []
    for c in comps:
        if c['kind'] == 'firmware':
            fi = firmware_internal(root, tree, c, top_names)
            if fi['core_user_files'] or fi['extras'] or fi['user_unlayered']:
                internal.append(fi)
            ioc += ioc_issues(root, c)
    table = readme_table(root)
    readme = {'has_table': table is not None, 'missing': [], 'stale': []}
    if table is not None:
        tl = [p.lower() for p in table]
        required = [n for n, isdir in ents if isdir and not n.startswith('.') and n.lower() not in ROOT_ENV_DIRS
                    and (tracked_top is None or n.lower() in tracked_top or n.lower() == 'release')]
        required += [c['root'] for c in comps if c['ok'] and c['root'].count('/') == 1]
        for req in required:
            rq = req.lower()
            if not any(p == rq or p.startswith(rq + '/') for p in tl):
                readme['missing'].append(req)
        # release/ 是 gitignore 的交付目录，新建或刚 clone 的工程里本来就不存在，不算失效
        readme['stale'] = [p for p in table if first_seg(p).lower() != 'release'
                           and not os.path.exists(os.path.join(root, p))]
    # 计数口径：组件、根目录项、目录、命名逐条计；固件内部的 Core 自写文件与 User 未分层每个组件各计 1 条
    issues = (sum(1 for c in comps if c['ok'] is False and not c.get('review')) + len(viol) + len(unknown) + len(ioc)
              + sum(bool(x['core_user_files']) + len(x['extras']) + bool(x['user_unlayered']) for x in internal)
              + len(readme['missing']) + len(readme['stale']) + (0 if readme['has_table'] else 1))
    for c in comps:
        c.pop('refs', None)
    return {'declared': declared, 'workspace': ws, 'components': comps, 'moves': moves, 'root_violations': viol,
            'unknown_dirs': unknown, 'readme': readme, 'ioc_issues': ioc, 'firmware_internal': internal,
            'issues': issues}


def quick_layout(root: str, git: dict) -> list[str]:
    """已声明 layout = 1 的工程：只看根目录与 README 目录表是否回退（毫秒级）。"""
    ents = root_entries(root)
    ws = workspace_kind(root, {n.lower() for n, _ in ents})
    tracked_top = {first_seg(t).lower() for t in git['tracked']}
    viol, unknown = root_checks(root, ents, ws, set(), tracked_top)
    out = [f'布局：根目录 {v["path"]}（{v["hint"]}）' for v in viol]
    out += [f'布局：词表外顶层目录 {u["path"]}（{u["hint"]}）' for u in unknown if u['tracked'] is not False]
    table = readme_table(root)
    if table is None:
        out.append('布局：根 README 缺少「## 目录结构」表')
    else:
        tl = [p.lower() for p in table]
        for n, isdir in ents:
            nl = n.lower()
            if isdir and not n.startswith('.') and nl in tracked_top and nl not in ROOT_ENV_DIRS \
                    and not any(p == nl or p.startswith(nl + '/') for p in tl):
                out.append(f'布局：README 目录表未说明 {n}/')
    return out


# ---------------------------------------------------------------- 文档型工程（规则见 references/文档整理.md）

IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.tif', '.tiff', '.ico', '.psd', '.ai', '.heic'}
ARCHIVE_EXT = {'.zip', '.7z', '.rar', '.tar', '.gz'}
SPEC_CATEGORIES = {'协议', '接口', '需求', '参考资料'}  # 规范本体：文件名可以带 _V主.次
NAME_VER_RE = re.compile(r'(^|[-_ ])[Vv]\d+(\.\d+)*($|[-_ ])')
CHECKSUM_RE = re.compile(r'(sha\d*|md5)sums?.*|.*清单.*\.json|.*manifest.*\.json', re.I)
PACKAGE_DIR_RE = re.compile(r'.*(定稿|素材|交付|打包|资源包|package).*', re.I)
MD_LINK_RE = re.compile(r'!?\[[^\]\n]*\]\(<?([^)\s>]+)>?(?:\s+"[^"]*")?\)')
HTML_REF_RE = re.compile(r'\b(?:href|src)\s*=\s*["\']([^"\']+)["\']', re.I)
CODE_SPAN_RE = re.compile(r'`([^`\n]+)`')
HISTORY_LINE_RE = re.compile(r'^\s*>?\s*[-*]?\s*v\d+(\.\d+)*\s*[（(]', re.I)  # 文档头的版本历史行，按原文保留
NARRATIVE_DOCS = ('docs/记录/', 'docs/报告/')  # 记录当时事实的文档，路径不追改
# 已按六类整理过的工程：方案/问题记录里疑似该归新四类的文件，只提示不计入（下次治理顺带重分）
RECLASS_RULES = [
    (re.compile(r'纪要|推进记录|过程记录|生图记录|审查记录|会议记录|选型记录'), '记录'),
    (re.compile(r'使用说明|用户手册|操作手册|手册|指南'), '手册'),
    (re.compile(r'周报|月报|季报|复盘|调研报告|分析报告|研究报告'), '报告'),
    (re.compile(r'模板|范本'), '模板'),
]


def reclass_hints(files) -> list[dict]:
    out = []
    for rel, _, _ in files:
        parts = rel.split('/')
        if len(parts) < 3 or parts[1] not in ('方案', '问题记录'):
            continue
        stem = posixpath.splitext(parts[-1])[0]
        to = next((c for rx, c in RECLASS_RULES if rx.search(stem)), None)
        if to:
            out.append({'path': rel, 'to': f'docs/{to}/'})
    return out


def docs_dir_hint(name: str) -> str:
    if DATED_RX.fullmatch(name) or PACKAGE_DIR_RE.fullmatch(name):
        return ('素材包：图片拆进 assets/定稿/<NN_用途>/，浏览页留 assets/定稿/ 根，说明进 docs/手册/，记录进 docs/记录/，'
                '校验和与清单进回收站；内容重复的整包进回收站')
    if re.fullmatch(r'docs?|documents?|文档|资料|说明|方案', name, re.I):
        return '文档 → docs/ 十类'
    if re.fullmatch(r'scripts?|src|源码|代码|code', name, re.I):
        return '脚本 → tools/'
    if re.search(r'参考|原图|素材|图片|images?|img|设定|设计|视觉', name, re.I):
        return '素材 → assets/参考/（给 AI 的输入）、assets/原图/（AI 或外部原件）、assets/定稿/（选定交付）；脚本 → tools/'
    return '词表外目录：归入 docs/ assets/ tools/ release/，归不进再向用户申请新增词'


def docs_root_hint(name: str) -> str:
    ext = posixpath.splitext(name)[1].lower()
    if ext in DOC_EXT:
        return '文档 → docs/<类>/（去掉工程名前缀、日期和版本号）'
    if ext in SCRIPT_EXT:
        return '脚本 → tools/'
    if ext in IMG_EXT:
        return '素材 → assets/<参考|原图|定稿>/'
    if ext in ARCHIVE_EXT:
        return '压缩包：对外发出的最近一次进 release/，其余解压归位或进回收站'
    return '根目录只放 README.md、VERSION、CLAUDE.md'


def docs_root_checks(root: str, ents: list[tuple[str, bool]]):
    viol, unknown = [], []
    for n, isdir in ents:
        nl = n.lower()
        if isdir:
            if nl not in DOCS_LAYOUT_V1 and nl not in ROOT_CONFIG_DIRS:
                unknown.append({'path': n + '/', 'hint': docs_dir_hint(n), 'tracked': None})
        elif not ROOT_FILE_OK.fullmatch(n):
            viol.append({'path': n, 'hint': docs_root_hint(n)})
    return viol, unknown


def docs_readme(root: str, ents: list[tuple[str, bool]]) -> dict:
    """根 README 三节：目录结构表（双向校验）、权威文档、素材取用（有 assets/ 时）。"""
    table = readme_table(root)
    rd = {'has_table': table is not None, 'missing': [], 'stale': [], 'sections_missing': []}
    if table is not None:
        tl = [p.lower() for p in table]
        for n, isdir in ents:
            nl = n.lower()
            if isdir and not n.startswith('.') and not any(p == nl or p.startswith(nl + '/') for p in tl):
                rd['missing'].append(n)
        rd['stale'] = [p for p in table if first_seg(p).lower() != 'release'
                       and not os.path.exists(os.path.join(root, p))]
    name = next((n for n, isdir in ents if not isdir and n.lower() == 'readme.md'), None)
    text = read_text(os.path.join(root, name)) if name else ''
    need = ['权威文档'] + (['素材取用'] if any(isdir and n.lower() == 'assets' for n, isdir in ents) else [])
    rd['sections_missing'] = [s for s in need if not re.search(rf'^#{{2,3}}\s*{s}', text, re.M)]
    return rd


def link_target(base_dir: str, target: str):
    t = target.strip()
    if not t or re.match(r'^([a-z][a-z0-9+.-]*:|#|//)', t, re.I):
        return None  # 外链、锚点、data:/mailto:、盘符路径
    t = urllib.parse.unquote(t.split('#', 1)[0].split('?', 1)[0]).replace('\\', '/')
    if not t or t.startswith('/'):
        return None
    return posixpath.normpath(posixpath.join(base_dir, t))


def code_span_paths(text: str, skip_table: bool):
    """反引号里像本地路径的文本（含 / 且以 / 结尾或带扩展名）；版本历史行不看。"""
    for i, line in enumerate(text.splitlines(), 1):
        if HISTORY_LINE_RE.match(line) or (skip_table and line.lstrip().startswith('|')):
            continue
        for m in CODE_SPAN_RE.finditer(line):
            p = m.group(1).strip().replace('\\', '/')
            if '/' not in p or re.search(r'[\s<>*{}|]|://', p) or p.startswith(('/', '~', '$', '.')) \
                    or re.match(r'^[A-Za-z]:', p):
                continue
            last = p.rstrip('/').rsplit('/', 1)[-1]
            if p.endswith('/') or '.' in last:
                yield i, p


def docs_links(root: str, tree: Tree) -> dict:
    """md/html 里的相对链接必须能打开（计入）；md 反引号里的路径失效只列复核（不计入）。
    反引号路径是某个现存路径的尾段（表格里相对上下文目录写的子路径）、或是词表规定的标准位置，都不列。"""
    broken, review = [], []
    known = [f[0] for f in tree.files] + tree.dirs
    canon = set(DOCS_LAYOUT_V1) | {f'docs/{c}' for c in DOC_CATEGORIES} | {f'assets/{s}' for s in ASSET_STAGES}
    for rel, size, _ in tree.files:
        ext = posixpath.splitext(rel)[1].lower()
        if ext not in ('.md', '.html', '.htm') or rel.lower().startswith('release/') or size > 8_000_000:
            continue
        text = read_text(os.path.join(root, rel), 8_000_000)
        base = posixpath.dirname(rel)
        rx = MD_LINK_RE if ext == '.md' else HTML_REF_RE
        for i, line in enumerate(text.splitlines(), 1):
            for m in rx.finditer(line):
                t = link_target(base, m.group(1))
                if t and not os.path.exists(os.path.join(root, t)):
                    broken.append({'file': rel, 'line': i, 'target': m.group(1)})
        if ext != '.md' or rel.startswith(NARRATIVE_DOCS):
            continue
        for i, p in code_span_paths(text, skip_table='/' not in rel):
            q = p.rstrip('/')
            if q in canon or os.path.exists(os.path.join(root, q)) or os.path.exists(os.path.join(root, base, q)) \
                    or any(k.endswith('/' + q) for k in known):
                continue
            review.append({'file': rel, 'line': i, 'path': p})
    return {'broken': broken, 'review': review}


def docs_duplicates(root: str, tree: Tree) -> list[dict]:
    by_size: dict[int, list[str]] = {}
    for rel, size, _ in tree.files:
        if size and not rel.lower().startswith('release/'):
            by_size.setdefault(size, []).append(rel)
    out = []
    for size, rels in sorted(by_size.items()):
        if len(rels) < 2:
            continue
        by_hash: dict[str, list[str]] = {}
        for r in rels:
            by_hash.setdefault(sha1(os.path.join(root, r)), []).append(r)
        out += [{'size': size, 'files': sorted(rs)} for h, rs in by_hash.items() if h and len(rs) > 1]
    return out


def docs_tree_checks(tree: Tree, name: str) -> tuple[dict, dict, list, list, list]:
    docs = {'unsorted': [], 'dated_names': [], 'prefixed': [], 'report_shape': [],
            'reclass_hints': reclass_hints(tree.files_under('docs'))}
    for rel, _, _ in tree.files_under('docs'):
        parts = rel.split('/')
        stem = posixpath.splitext(parts[-1])[0]
        if len(parts) < 3 or parts[1] not in DOC_CATEGORIES:
            docs['unsorted'].append(rel)
            continue
        if parts[1] == '报告':
            if len(parts) != 4:
                docs['report_shape'].append(rel)
            continue  # 报告按期命名，允许带日期
        if DATE_RE.search(stem) or (parts[1] not in SPEC_CATEGORIES and NAME_VER_RE.search(stem)):
            docs['dated_names'].append(rel)
        if name and re.match(re.escape(name) + r'[-_ ]', stem):
            docs['prefixed'].append(rel)
    assets = {'loose': [], 'bad_stage': []}
    for rel, _, _ in tree.files_under('assets'):
        parts = rel.split('/')
        if len(parts) == 2:
            assets['loose'].append(rel)
        elif parts[1] not in ASSET_STAGES:
            if f'assets/{parts[1]}/' not in assets['bad_stage']:
                assets['bad_stage'].append(f'assets/{parts[1]}/')
        elif parts[1] == '定稿' and len(parts) == 3 and posixpath.splitext(rel)[1].lower() not in ('.html', '.htm'):
            assets['loose'].append(rel)  # 定稿/ 根目录只放浏览页，素材按用途进子目录
    dated_dirs = [d + '/' for d in tree.dirs if '/' in d and not d.lower().startswith(('release/', 'docs/报告/'))
                  and DATED_RX.fullmatch(posixpath.basename(d))]
    checksums = [rel for rel, _, _ in tree.files if CHECKSUM_RE.fullmatch(posixpath.basename(rel))
                 and not rel.lower().startswith('release/')]
    sub_readmes = [rel for rel, _, _ in tree.files if '/' in rel and posixpath.basename(rel).lower() == 'readme.md']
    return docs, assets, dated_dirs, checksums, sub_readmes


def check_release_docs(root: str, name: str) -> list[str]:
    rdir = os.path.join(root, 'release')
    if not os.path.isdir(rdir):
        return []
    issues, dated = [], []
    ents = sorted(os.scandir(rdir), key=lambda x: x.name)
    subdirs = [e.name for e in ents if e.is_dir()]
    if subdirs:
        issues.append('release/ 下有子目录（只放打包文件，不放展开的包目录）：' + '、'.join(subdirs[:5]))
    rx = re.compile(re.escape(name) + r'_.+_((?:19|20)\d{6})\.[A-Za-z0-9]+')
    for e in ents:
        if e.is_dir():
            continue
        m = rx.fullmatch(e.name)
        if m:
            dated.append((m.group(1), e.name))
        else:
            issues.append(f'release/{e.name}：文件名应为 {name}_<内容>_<YYYYMMDD>.<扩展名>')
    if dated:
        latest = max(d for d, _ in dated)
        old = [f for d, f in dated if d < latest]
        if old:
            issues.append('release/ 只留最近一次，旧包进回收站：' + '、'.join(old))
    return issues


def quick_docs(root: str, name: str) -> list[str]:
    """已声明的文档型工程：根目录、docs/ 与 assets/ 第一层、README 目录表、release（毫秒级）。"""
    ents = root_entries(root)
    viol, unknown = docs_root_checks(root, ents)
    out = [f'布局：根目录 {v["path"]}（{v["hint"]}）' for v in viol]
    out += [f'布局：词表外顶层目录 {u["path"]}（{u["hint"]}）' for u in unknown]
    for top, allowed, what in (('docs', DOC_CATEGORIES, '十类'), ('assets', ASSET_STAGES, '参考/原图/定稿')):
        try:
            sub = list(os.scandir(os.path.join(root, top)))
        except OSError:
            continue
        for e in sub:
            if not e.is_dir():
                out.append(f'布局：{top}/{e.name} 直接放在 {top}/ 下（应归入{what}）')
            elif e.name not in allowed:
                out.append(f'布局：{top}/{e.name}/ 不在{what}里')
    rd = docs_readme(root, ents)
    if not rd['has_table']:
        out.append('布局：根 README 缺少「## 目录结构」表')
    out += [f'布局：README 目录表未说明 {n}/' for n in rd['missing']]
    out += [f'release：{x}' for x in check_release_docs(root, name)]
    return out


def scan_docs(root: str, quick: bool = False, want_fp: bool = False) -> dict:
    t0 = time.time()
    root = os.path.abspath(root)
    vinfo = check_version(root, None)
    data = vinfo.get('data') or {}
    declared = 1 if data.get('type', '').lower() == 'docs' and data.get('layout', '') == LAYOUT_VERSION else None
    name = data.get('name') or os.path.basename(root)
    if not vinfo['exists']:
        vinfo['issues'] = ['缺少根目录 VERSION（文档型工程写 type = docs、name、layout = 1）']
    res: dict = {'mode': 'quick' if quick else 'project', 'type': 'docs', 'root': root,
                 'name': os.path.basename(root), 'errors': [], 'git': None, 'toolchains': [], 'entries': [],
                 'protected': []}
    if quick:
        issues = [f'VERSION：{x}' for x in vinfo['issues']]
        if declared:
            issues += quick_docs(root, name)
        res['issues'] = issues
        res['elapsed'] = round(time.time() - t0, 2)
        return res

    tree = Tree(root)
    res['errors'] = tree.errors[:50]
    a: dict = {'not_git': False, 'worktrees': [], 'candidates': [], 'doc_groups': [], 'copies': [],
               'nested_repos': [], 'reference_projects': [], 'big_untracked': []}
    claimed: list[str] = []
    for rel in sorted(tree.git_markers):
        size, n, _ = tree.size_under(rel)
        a['nested_repos'].append({'path': rel, 'head': '', 'remote': '', 'size': size, 'files': n})
        claimed.append(rel.lower())
    for rel in sorted(tree.dirs, key=str.lower):
        rl = rel.lower()
        if under_any(rl, claimed) or under(rl, 'release'):
            continue
        hit = match_dir(rel, skip_dated=True)  # 带日期的目录由布局检查报告
        if hit:
            size, n, mt = tree.size_under(rel)
            a['candidates'].append({'category': hit[0], 'confidence': hit[1], 'path': rel + '/', 'kind': 'dir',
                                    'size': size, 'files': n, 'mtime': day(mt), 'tracked': 'none',
                                    'contains_project': []})
            claimed.append(rl)
    for rel, size, mt in tree.files:
        if under_any(rel.lower(), claimed) or under(rel.lower(), 'release'):
            continue
        hit = match_file(rel)
        if hit:
            a['candidates'].append({'category': hit[0], 'confidence': hit[1], 'path': rel, 'kind': 'file',
                                    'size': size, 'files': 1, 'mtime': day(mt), 'tracked': 'none'})
    collapse_files(a, tree, set(), claimed, [], set())
    groups: dict[tuple[str, str], list] = {}
    for rel, _, mt in tree.files:
        stem, ext = posixpath.splitext(posixpath.basename(rel))
        if ext.lower() not in DOC_EXT or under_any(rel.lower(), claimed) or rel.lower().startswith('release/') \
                or rel.startswith('docs/报告/') or stem.lower().startswith('readme'):
            continue
        groups.setdefault((posixpath.dirname(rel), topic_key(stem)), []).append((rel, mt))
    for (d, key), items in sorted(groups.items()):
        if len(items) >= 2:
            items.sort(key=lambda x: x[1])
            a['doc_groups'].append({'dir': d or '.', 'topic': key,
                                    'files': [{'path': r, 'mtime': day(m)} for r, m in items]})

    ents = root_entries(root)
    viol, unknown = docs_root_checks(root, ents)
    docs, assets, dated_dirs, checksums, sub_readmes = docs_tree_checks(tree, name)
    dups = docs_duplicates(root, tree)
    links = docs_links(root, tree)
    rd = docs_readme(root, ents)
    issues = (len(viol) + len(unknown) + len(dated_dirs) + len(docs['unsorted']) + len(docs['dated_names'])
              + len(docs['prefixed']) + len(docs['report_shape']) + len(assets['loose']) + len(assets['bad_stage'])
              + len(checksums) + len(sub_readmes) + len(dups) + len(links['broken'])
              + len(rd['missing']) + len(rd['stale']) + len(rd['sections_missing']) + (0 if rd['has_table'] else 1))
    b = {'type': 'docs', 'declared': declared, 'root_violations': viol, 'unknown_dirs': unknown,
         'dated_dirs': dated_dirs, 'docs': docs, 'assets': assets, 'checksums': checksums,
         'sub_readmes': sub_readmes, 'duplicates': dups, 'links': links, 'readme': rd, 'issues': issues}
    cc = {'branches': [], 'version': vinfo, 'release': check_release_docs(root, name)}
    res['phase_a'], res['phase_b'], res['phase_c'] = a, b, cc
    na = len(a['candidates']) + len(a['doc_groups']) + len(a['nested_repos'])
    res['counts'] = {'a': na, 'a_size': sum(x['size'] for k in ('candidates', 'nested_repos') for x in a[k]),
                     'b': issues if declared else 0, 'b_advice': 0 if declared else issues,
                     'c': len(vinfo['issues']) + len(cc['release'])}
    if want_fp:
        res['_fp'] = fingerprint(tree.files, '', [c.rstrip('/') for c in claimed])
    res['elapsed'] = round(time.time() - t0, 2)
    return res


# ---------------------------------------------------------------- 主扫描

def fingerprint(files, base: str, exclude: list[str]) -> tuple[set[str], set[tuple[str, int]]]:
    structure, content = set(), set()
    bl = base.lower()
    ex = [e.lower() for e in exclude]
    for rel, size, _ in files:
        rl = rel.lower()
        if ex and under_any(rl, ex):
            continue
        sub = rl[len(bl) + 1:] if bl else rl
        parts = sub.split('/')
        if any(p in VENDOR_NAMES for p in parts[:-1]):
            continue
        if posixpath.splitext(sub)[1] not in SRC_EXT:
            continue
        structure.add(sub)
        content.add((sub, size))
    return structure, content


def overlap(a: tuple[set, set], b: tuple[set, set]):
    sa, ca = a
    sb, cb = b
    m = min(len(sa), len(sb))
    if m < 10:
        return None
    s = len(sa & sb) / m
    c = len(ca & cb) / max(1, min(len(ca), len(cb)))
    return s, c


def scan_project(root: str, quick: bool = False, want_fp: bool = False, docs: bool = False) -> dict:
    if docs or project_type(root) == 'docs':
        return scan_docs(root, quick, want_fp)
    t0 = time.time()
    root = os.path.abspath(root)
    res: dict = {'mode': 'quick' if quick else 'project', 'root': root, 'name': os.path.basename(root),
                 'errors': []}
    git = git_state(root, quick)
    res['git'] = None if not git else {k: v for k, v in git.items()
                                      if k not in ('tracked', 'untracked', 'untracked_dirs', 'changed')}
    tracked_l = {t.lower() for t in git['tracked']} if git else set()
    if quick:
        return finish_quick(root, res, git, tracked_l, t0)

    tree = Tree(root)
    res['errors'] = tree.errors[:50]
    rels = [f[0] for f in tree.files]
    entries = find_entries(rels)
    res['toolchains'] = sorted({k for k, _ in entries})
    res['entries'] = [r for _, r in entries]
    prot, keil = protection(root, entries)
    res['protected'] = sorted(prot)
    untracked_l = {u.lower() for u in git['untracked']} if git else {f[0].lower() for f in tree.files}

    a: dict = {'not_git': git is None, 'worktrees': (git or {}).get('worktrees', []),
               'candidates': [], 'doc_groups': [], 'copies': [], 'nested_repos': [], 'big_untracked': []}
    if os.path.isdir(os.path.join(root, '.claude', 'worktrees')):
        a['worktrees'].append('.claude/worktrees/（残留目录）')
    claimed: list[str] = []

    # G 嵌套仓库
    submodules = set()
    gm = os.path.join(root, '.gitmodules')
    if os.path.isfile(gm):
        submodules = {m.group(1).strip().lower() for m in re.finditer(r'path\s*=\s*(.+)', read_text(gm))}
    for rel in sorted(tree.git_markers):
        if rel.lower() in submodules:
            continue
        gdir = os.path.join(root, rel, '.git')
        head, remote = '', ''
        if os.path.isdir(gdir):
            head = read_text(os.path.join(gdir, 'HEAD')).strip().replace('ref: refs/heads/', '')
            m = re.search(r'url\s*=\s*(\S+)', read_text(os.path.join(gdir, 'config')))
            remote = m.group(1) if m else ''
        size, n, mt = tree.size_under(rel)
        a['nested_repos'].append({'path': rel, 'head': head, 'remote': remote, 'size': size, 'files': n})
        claimed.append(rel.lower())

    # A–D 过程文件候选（目录）
    for rel in sorted(tree.dirs, key=str.lower):
        rl = rel.lower()
        if under_any(rl, claimed) or is_protected(rl, prot) or rl == 'release' or rl.startswith('release/'):
            continue
        hit = match_dir(rel)
        if not hit:
            continue
        inner = sorted({r for _, r in entries if under(r.lower(), rl)})
        if hit == ('A', '复核') and inner and DATED_RX.fullmatch(posixpath.basename(rel)):
            continue  # 名字带日期但内含工程入口的（如厂商 SDK 包），交给副本/参考检测
        size, n, mt = tree.size_under(rel)
        ntr = sum(1 for f in tree.files_under(rel) if f[0].lower() in tracked_l)
        a['candidates'].append({'category': hit[0], 'confidence': hit[1], 'path': rel + '/', 'kind': 'dir',
                                'size': size, 'files': n, 'mtime': day(mt),
                                'tracked': 'none' if ntr == 0 else 'all' if ntr == n else f'{ntr}/{n}',
                                'contains_project': inner[:5]})
        claimed.append(rl)

    # G' 参考工程：位于 _ref/、参考*/、开发资料/、例程*/ 下、带工程入口的目录 → 应迁参考库
    a['reference_projects'] = []
    for _, r in entries:
        rl = r.lower()
        if under_any(rl, claimed) or is_protected(rl, prot):
            continue
        parts = r.split('/')[:-1]
        idx = next((i for i, p in enumerate(parts) if REF_RE.fullmatch(p)), None)
        if idx is None:
            continue
        ref_root = '/'.join(parts[:idx + 2]) if idx + 1 < len(parts) else '/'.join(parts[:idx + 1])
        if under_any(ref_root.lower(), claimed):
            continue
        size, n, mt = tree.size_under(ref_root)
        a['reference_projects'].append({'path': ref_root, 'size': size, 'files': n})
        claimed.append(ref_root.lower())

    # F 工程内副本：入口目录两两比较（已被认领的目录不再参与）
    entry_dirs = sorted({posixpath.dirname(r) for _, r in entries
                         if not under_any(r.lower(), claimed) and not is_protected(r.lower(), prot)
                         and not any(p in VENDOR_NAMES for p in r.lower().split('/')[:-1])})[:40]
    fps = {}
    for i, d1 in enumerate(entry_dirs):
        for d2 in entry_dirs[i + 1:]:
            if under_any(d1.lower(), claimed) or under_any(d2.lower(), claimed):
                continue
            key1 = (d1, d2 if under(d2.lower(), d1.lower()) else '')
            key2 = (d2, d1 if under(d1.lower(), d2.lower()) else '')
            if key1 not in fps:
                fps[key1] = fingerprint(tree.files_under(d1), d1, [key1[1]] if key1[1] else [])
            if key2 not in fps:
                fps[key2] = fingerprint(tree.files_under(d2), d2, [key2[1]] if key2[1] else [])
            ov = overlap(fps[key1], fps[key2])
            if ov and ov[0] >= 0.6:
                copy = d2 if len(d2) >= len(d1) else d1
                other = d1 if copy == d2 else d2
                size, n, mt = tree.size_under(copy)
                a['copies'].append({'path': copy or '.', 'like': other or '.', 'structure': round(ov[0], 2),
                                    'content': round(ov[1], 2), 'strength': '强' if ov[1] >= 0.5 else '弱',
                                    'size': size, 'files': n})
                if copy:
                    claimed.append(copy.lower())

    # 散落文件 + Keil 输出目录中的非当前产物
    keil_dirs = {}
    for k in keil:
        keil_dirs.setdefault(k['outdir'].lower(), set()).add(k['outname'].lower())
    for rel, size, mt in tree.files:
        rl = rel.lower()
        if under_any(rl, claimed) or rl.startswith('release/'):
            continue
        parent, name = posixpath.split(rel)
        hit = None
        if parent.lower() in keil_dirs:
            stem, ext = posixpath.splitext(name.lower())
            if ext in ('.hex', '.bin', '.axf', '.elf') and stem not in keil_dirs[parent.lower()]:
                hit = ('C', '高')
        elif not is_protected(rl, prot):
            hit = match_file(rel)
        if hit:
            a['candidates'].append({'category': hit[0], 'confidence': hit[1], 'path': rel, 'kind': 'file',
                                    'size': size, 'files': 1, 'mtime': day(mt),
                                    'tracked': 'all' if rl in tracked_l else 'none'})
    for _ in range(2):
        collapse_files(a, tree, tracked_l, claimed, [r for _, r in entries], prot)

    # E 同主题多轮文档 + 规范本体
    groups: dict[tuple[str, str], list] = {}
    specs = []
    for rel, size, mt in tree.files:
        rl = rel.lower()
        stem, ext = posixpath.splitext(posixpath.basename(rel))
        if ext.lower() not in DOC_EXT or under_any(rl, claimed) or is_protected(rl, prot):
            continue
        parts_l = rl.split('/')
        if any(p in VENDOR_NAMES for p in parts_l[:-1]) or rl.startswith('release/'):
            continue
        in_spec_dir = any(p in SPEC_DIR_NAMES for p in parts_l[:-1])
        if is_spec_name(stem) or (in_spec_dir and not PROCESS_RE.search(stem)):
            token, ver = spec_identity(stem)
            specs.append({'path': rel, 'topic': token, 'version': ver, 'size': size})
            continue
        if stem.lower().startswith('readme') or LICENSE_RE.fullmatch(posixpath.basename(rel)):
            continue
        groups.setdefault((posixpath.dirname(rel), topic_key(stem)), []).append((rel, mt))
    for (d, key), items in sorted(groups.items()):
        if len(items) >= 2:
            items.sort(key=lambda x: x[1])
            a['doc_groups'].append({'dir': d or '.', 'topic': key,
                                    'files': [{'path': r, 'mtime': day(m)} for r, m in items]})
    res['spec_docs'] = specs

    # 未跟踪大件 / 二进制
    for rel, size, mt in tree.files:
        rl = rel.lower()
        if rl not in untracked_l or under_any(rl, claimed) or is_protected(rl, prot) or rl.startswith('release/'):
            continue
        if size > BIG_FILE or posixpath.splitext(rl)[1] in BIN_EXT:
            a['big_untracked'].append({'path': rel, 'size': size, 'mtime': day(mt)})

    # 阶段 C：版本规范
    cc: dict = {}
    cc['branches'] = branch_issues(git)
    vinfo = check_version(root, git)
    cc['version'] = vinfo
    cc['release'] = check_release(root, vinfo, tracked_l, git)

    # 阶段 B：目录布局（docs 六类归位 + layout v1）
    stray = [f[0] for f in tree.files if '/' not in f[0] and is_root_doc(f[0])]
    docs_unsorted = [f[0] for f in tree.files_under('docs')
                     if f[0].split('/')[1] not in DOC_CATEGORIES and not under_any(f[0].lower(), claimed)]
    a_root = {x['path'].rstrip('/').lower() for x in a['candidates'] + a['big_untracked']
              if '/' not in x['path'].rstrip('/')}
    b = scan_layout(root, tree, entries, prot, claimed, vinfo, tracked_l, git, a_root)
    b['docs'] = {'root_docs': stray, 'docs_unsorted': len(docs_unsorted), 'docs_unsorted_sample': docs_unsorted[:15],
                 'reclass_hints': reclass_hints(tree.files_under('docs'))}
    res['phase_a'], res['phase_b'], res['phase_c'] = a, b, cc
    res['counts'] = counts(a, b, cc)
    res['protected'] = sorted(p for p in prot if not under_any(p, claimed) and os.path.isdir(os.path.join(root, p)))
    if want_fp:
        res['_fp'] = fingerprint(tree.files, '', [c.rstrip('/') for c in claimed])
    res['elapsed'] = round(time.time() - t0, 2)
    return res


def collapse_files(a: dict, tree: Tree, tracked_l: set[str], claimed: list[str], entry_rels: list[str],
                   prot: set[str]):
    """同一目录下有 ≥3 个候选、且候选覆盖该目录过半文件时，合并为一个目录候选，避免逐行刷屏。
    含工程入口、构建保护路径、其他已认领子目录的目录，以及源码/规范/docs 目录本身，一律不合并。"""
    by_parent: dict[str, list[dict]] = {}
    for c in a['candidates']:
        parent = posixpath.dirname(c['path'].rstrip('/'))
        if parent:
            by_parent.setdefault(parent, []).append(c)
    for parent, hits in by_parent.items():
        pl = parent.lower()
        size, n, mt = tree.size_under(parent)
        if len(hits) < 3 or sum(h['files'] for h in hits) / max(n, 1) < 0.5 or under_any(pl, claimed):
            continue
        if posixpath.basename(pl) in SOURCE_ROOTS | SPEC_DIR_NAMES | {'docs', 'doc'}:
            continue
        mine = {h['path'].rstrip('/').lower() for h in hits}
        if any(under(e.lower(), pl) for e in entry_rels) or any(under(p, pl) for p in prot) \
                or any(under(c, pl) and c not in mine for c in claimed):
            continue
        for h in hits:
            a['candidates'].remove(h)
        claimed[:] = [c for c in claimed if c not in mine]
        ntr = sum(1 for f in tree.files_under(parent) if f[0].lower() in tracked_l)
        cats = [h['category'] for h in hits]
        a['candidates'].append({'category': max(set(cats), key=cats.count),
                                'confidence': '高' if all(h['confidence'] == '高' for h in hits) else '复核',
                                'path': parent + '/', 'kind': 'dir', 'size': size, 'files': n, 'mtime': day(mt),
                                'tracked': 'none' if ntr == 0 else 'all' if ntr == n else f'{ntr}/{n}',
                                'contains_project': [], 'note': f'合并了 {len(hits)} 个候选'})
        claimed.append(pl)


def is_root_doc(name: str) -> bool:
    stem, ext = posixpath.splitext(name)
    ext = ext.lower()
    if ext not in DOC_EXT or ROOT_ALLOWED.fullmatch(stem) or ROOT_ALLOWED.fullmatch(name):
        return False
    if ext in ('.html', '.htm') and not CJK_RE.search(stem):
        return False  # index.html、dev.html 这类是前端入口，不是文档
    return True


def branch_issues(git: dict | None) -> list[dict]:
    if not git:
        return []
    out = []
    for bi in git.get('branch_info', []):
        name = bi['name']
        if BRANCH_OK.fullmatch(name):
            continue
        if name == 'master':
            advice = '改名为 main'
        elif bi['merged']:
            advice = '已合入主线，可删除'
        elif TASK_BRANCH.fullmatch(name):
            advice = f'任务分支，未合并（领先 {bi["ahead_of_base"]} 个提交）：合并 / 删除 / 改名，需确认'
        else:
            advice = f'疑似变体或旧版（领先 {bi["ahead_of_base"]} 个提交）：改名为 protocol/vX.Y-<变体>，需确认协议版本'
        out.append({'name': name, 'current': name == git.get('branch'), 'merged': bi['merged'],
                    'ahead': bi['ahead_of_base'], 'advice': advice})
    return out


def counts(a: dict, b: dict, c: dict) -> dict:
    """b 为布局待处理项：docs 归位一直计入；layout v1 各项只在 VERSION 声明 layout = 1 后计入，否则记为建议。"""
    na = (len(a['candidates']) + len(a['doc_groups']) + len(a['copies']) + len(a['nested_repos'])
          + len(a['reference_projects']) + len(a['big_untracked']) + len(a['worktrees']) + (1 if a['not_git'] else 0))
    size = sum(x['size'] for k in ('candidates', 'copies', 'nested_repos', 'reference_projects', 'big_untracked')
               for x in a[k])
    nd = len(b['docs']['root_docs']) + (1 if b['docs']['docs_unsorted'] else 0)
    nb = nd + (b['issues'] if b['declared'] else 0)
    nc = len(c['branches']) + len(c['version']['issues']) + len(c['release'])
    return {'a': na, 'a_size': size, 'b': nb, 'b_advice': 0 if b['declared'] else b['issues'], 'c': nc}


def finish_quick(root: str, res: dict, git: dict | None, tracked_l: set[str], t0: float) -> dict:
    issues = []
    if not git:
        issues.append('非 git 工程')
    else:
        tracked = git['tracked']
        prot, _ = protection(root, find_entries(tracked))
        seen = set()
        for path in git['untracked_dirs'] + git['changed']:
            p = path.rstrip('/')
            parts = p.split('/')
            hit = None
            for i in range(1, len(parts) + 1):
                sub = '/'.join(parts[:i])
                sl = sub.lower()
                if is_protected(sl, prot) or sl == 'release' or sl.startswith('release/'):
                    break
                if i < len(parts) or path.endswith('/'):
                    m = match_dir(sub)
                else:
                    m = match_file(sub)
                if m:
                    hit = (sub + ('/' if i < len(parts) or path.endswith('/') else ''), m)
                    break
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                issues.append(f'过程文件（{CATEGORY_NAMES[hit[1][0]]}）：{hit[0]}')
        for w in git['worktrees']:
            issues.append(f'存在额外 worktree：{w}')
        vinfo = check_version(root, git)
        if not vinfo['exists']:
            issues.append('未接入 one-project 版本规范（需要时运行治理模式）')
        else:
            issues += [f'分支名不合规：{b}' for b in git['branches'] if not BRANCH_OK.fullmatch(b)]
            issues += [f'VERSION：{x}' for x in vinfo['issues']]
            issues += [f'release：{x}' for x in check_release(root, vinfo, tracked_l, git)]
            if (vinfo.get('data') or {}).get('layout', '') == LAYOUT_VERSION:
                issues += quick_layout(root, git)
    res['issues'] = issues
    res['elapsed'] = round(time.time() - t0, 2)
    return res


# ---------------------------------------------------------------- 工作区

def scan_workspace(roots: list[str]) -> dict:
    t0 = time.time()
    projects, clutter = [], []
    for r in roots:
        r = os.path.abspath(r)
        try:
            children = sorted(os.scandir(r), key=lambda e: e.name.lower())
        except OSError as e:
            clutter.append({'path': r, 'note': f'无法读取：{e.strerror}', 'size': 0})
            continue
        for e in children:
            if not e.is_dir() or e.is_symlink():
                continue
            if e.name.startswith('.'):
                t = Tree(e.path)
                clutter.append({'path': e.path, 'note': '工作区根目录下的隐藏目录', 'size': t.size_under('')[0]})
                continue
            p = scan_project(e.path, want_fp=True)
            p['workspace_root'] = r
            projects.append(p)
    # 跨工程副本
    fps = {p['root']: p.pop('_fp') for p in projects}
    copies = []
    for i, p1 in enumerate(projects):
        for p2 in projects[i + 1:]:
            ov = overlap(fps[p1['root']], fps[p2['root']])
            if ov and ov[0] >= 0.6:
                copies.append({'a': p1['root'], 'b': p2['root'], 'structure': round(ov[0], 2),
                               'content': round(ov[1], 2), 'strength': '强' if ov[1] >= 0.5 else '弱'})
    # 跨工程规范本体
    by_key: dict[tuple[str, str], list] = {}
    for p in projects:
        for s in p.get('spec_docs', []):
            if s['version']:
                by_key.setdefault((s['topic'], s['version']), []).append((p['name'], s['path'], p['root']))
    spec_groups = []
    for (topic, ver), items in sorted(by_key.items()):
        if len({x[0] for x in items}) < 2:
            continue
        hashes = {sha1(os.path.join(root, path)) for _, path, root in items}
        spec_groups.append({'topic': topic, 'version': ver, 'same_content': len(hashes) == 1,
                            'copies': [f'{n}/{path}' for n, path, _ in items]})
    return {'mode': 'workspace', 'roots': roots, 'projects': projects, 'clutter': clutter, 'copies': copies,
            'spec_groups': spec_groups, 'elapsed': round(time.time() - t0, 1)}


# ---------------------------------------------------------------- 输出

def md_table(headers: list[str], rows: list[list], limit: int = 80) -> str:
    def cell(x):
        return str(x).replace('|', '\\|').replace('\n', ' ')
    out = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    out += ['| ' + ' | '.join(cell(c) for c in r) + ' |' for r in rows[:limit]]
    if len(rows) > limit:
        out.append(f'\n…另有 {len(rows) - limit} 行，完整列表用 --json 查看')
    return '\n'.join(out)


TRACK_TEXT = {'all': '已跟踪', 'none': '未跟踪'}


def render_project(r: dict) -> str:
    if r.get('type') == 'docs':
        return render_docs(r)
    g = r['git']
    a, b, v, c = r['phase_a'], r['phase_b'], r['phase_c'], r['counts']
    lay_txt = f'布局项 **{c["b"]}**' + (f'（另有布局建议 {c["b_advice"]} 项，未声明 layout）' if c['b_advice'] else '')
    L = [f'# one-project 扫描：{r["name"]}', '',
         f'- 路径：`{r["root"]}`',
         f'- 耗时：{r["elapsed"]} 秒；清理项 **{c["a"]}**（约 {human(c["a_size"])}），{lay_txt}，规范项 **{c["c"]}**', '']
    rows = [['工具链', '、'.join(r['toolchains']) or '未识别']]
    if g:
        rows += [['git', f'{g["kind"]}，分支 `{g["branch"] or "(分离头)"}` @ {g["head"]}'],
                 ['未提交改动', f'{g["dirty_tracked"]} 个已跟踪文件'],
                 ['stash', g['stash']], ['远程', '；'.join(g['remotes']) or '无'],
                 ['分支', '、'.join(g['branches'])], ['tag 数', len(g['tags'])]]
        if not g['toplevel_ok']:
            rows.append(['⚠️ 注意', '该目录不是 git 仓库顶层'])
    else:
        rows.append(['git', '**非 git 工程**'])
    if not r['toolchains']:
        rows.append(['提示', '未识别到工具链：若这是文档/素材型工程，加 `--docs` 按文档规则扫描'])
    L += ['## 概况', '', md_table(['项', '值'], rows), '']

    L += ['## 阶段 A：清理', '']
    if a['not_git']:
        L += ['- ❗ 非 git 工程：先 `git init` + 按工具链生成 .gitignore + 提交现状基线', '']
    if a['worktrees']:
        L += ['### 额外 worktree', ''] + [f'- `{w}`' for w in a['worktrees']] + ['']
    if a['candidates']:
        rows = [[f'{x["category"]} {CATEGORY_NAMES[x["category"]]}',
                 f'`{x["path"]}`' + (' ⚑内含工程入口' if x.get('contains_project') else ''),
                 TRACK_TEXT.get(x['tracked'], x['tracked']), human(x['size']), x['files'], x['mtime'],
                 x['confidence']] for x in sorted(a['candidates'], key=lambda x: (x['category'], x['path']))]
        L += ['### 过程文件候选', '', md_table(['类别', '路径', '跟踪', '体积', '文件数', '最近修改', '把握'], rows), '']
    if a['doc_groups']:
        L += ['### E 同主题多轮文档（只留最终版，需判断）', '']
        for gr in a['doc_groups']:
            L.append(f'- **{gr["topic"]}**（`{gr["dir"]}`，{len(gr["files"])} 份）：'
                     + '；'.join(f'`{posixpath.basename(f["path"])}`({f["mtime"]})' for f in gr['files']))
        L.append('')
    if a['copies']:
        rows = [[f'`{x["path"]}`', f'`{x["like"]}`', x['structure'], x['content'], x['strength'], human(x['size'])]
                for x in a['copies']]
        L += ['### F 疑似工程副本', '', md_table(['疑似副本', '相似于', '结构重合', '内容重合', '强度', '体积'], rows), '']
    if a['nested_repos']:
        rows = [[f'`{x["path"]}`', x['head'], x['remote'] or '无', human(x['size'])] for x in a['nested_repos']]
        L += ['### G 嵌套仓库（迁参考库）', '', md_table(['路径', 'HEAD', '远程', '体积'], rows), '']
    if a['reference_projects']:
        rows = [[f'`{x["path"]}`', x['files'], human(x['size'])] for x in a['reference_projects']]
        L += ['### G 参考工程（非仓库，迁参考库）', '', md_table(['路径', '文件数', '体积'], rows), '']
    if a['big_untracked']:
        rows = [[f'`{x["path"]}`', human(x['size']), x['mtime']] for x in a['big_untracked']]
        L += ['### 未跟踪大件 / 二进制（不入库，直接回收站或进发布库）', '', md_table(['路径', '体积', '修改'], rows), '']
    if c['a'] == 0:
        L += ['✅ 无清理项', '']

    L += render_layout(b)

    L += ['## 阶段 C：版本规范', '']
    if v['branches']:
        rows = [[f'`{x["name"]}`' + (' ← 当前' if x['current'] else ''), x['advice']] for x in v['branches']]
        L += ['### 分支', '', md_table(['分支', '建议'], rows), '']
    vi = v['version']
    L += ['### VERSION', '']
    if vi['exists']:
        d = vi['data']
        L.append(f'- 当前：{d.get("product", "?")} V{d.get("version", "?")}（协议 {d.get("protocol", "?")}）')
    L += [f'- ❗ {x}' for x in vi['issues']] or ['- ✅ 一致']
    L.append('')
    if v['release']:
        L += ['### release/', ''] + [f'- ❗ {x}' for x in v['release']] + ['']
    if r.get('spec_docs'):
        rows = [[f'`{s["path"]}`', s['topic'], s['version'] or '未标版本'] for s in r['spec_docs']]
        L += ['## 规范本体文档（允许多版本并存）', '', md_table(['路径', '主题', '版本'], rows, 40), '']
    if r['protected']:
        L += ['## 构建产物保护路径（依据构建配置，不清理）', '',
              '、'.join(f'`{p}`' for p in r['protected'][:40]), '']
    if r['errors']:
        L += ['## 读取错误', ''] + [f'- {e}' for e in r['errors'][:20]] + ['']
    return '\n'.join(L)


def render_layout(b: dict) -> list[str]:
    mark = '❗' if b['declared'] else '💡'
    head = ('已声明 layout = 1，以下为待处理项' if b['declared']
            else '未声明 layout（VERSION 无 layout = 1），以下为迁移建议，不计入待处理')
    L = ['## 阶段 B：目录布局', '', f'- {head}' + (f'；生态工作区：{b["workspace"]}' if b['workspace'] else ''), '']
    docs = b['docs']
    if docs['root_docs'] or docs['docs_unsorted'] or docs.get('reclass_hints'):
        L += ['### 文档归位（docs/ 十类）', '']
        if docs['root_docs']:
            L.append('- ❗ 根目录散放文档：' + '、'.join(f'`{x}`' for x in docs['root_docs']))
        if docs['docs_unsorted']:
            L.append(f'- ❗ docs/ 下未归入十类的文件 {docs["docs_unsorted"]} 个，例：'
                     + '、'.join(f'`{x}`' for x in docs['docs_unsorted_sample'][:8]))
        L += [f'- 💡 疑似应归入新类（只提示，不计入，下次治理顺带重分）：`{h["path"]}` → `{h["to"]}`'
              for h in docs.get('reclass_hints', [])[:10]]
        L.append('')
    if b['components']:
        rows = [[x['type'], f'`{x["root"] or "."}`', f'`{x["entry"]}`',
                 '✅' if x['ok'] else ('-' if x['ok'] is None else
                                      ('🔍 复核 → ' if x.get('review') else f'{mark} → ') + f'`{x["expected"]}/`')]
                for x in b['components']]
        L += ['### 识别出的组件', '', md_table(['类型', '当前位置', '入口', '位置'], rows), '']
    if b['moves']:
        rows = [[f'`{m["from"]}`', f'`{m["to"]}`', m['why']] for m in b['moves']]
        L += ['### 建议迁移（git mv；构建配置改动需另行确认）', '', md_table(['原路径', '新路径', '依据'], rows), '']
    if b['root_violations']:
        L += ['### 根目录违规项', ''] + [f'- {mark} `{x["path"]}`：{x["hint"]}' for x in b['root_violations']] + ['']
    if b['unknown_dirs']:
        L += ['### 词表外顶层目录', ''] + [
            f'- {mark} `{x["path"]}`' + ('（未跟踪）' if x['tracked'] is False else '') + f'：{x["hint"]}'
            for x in b['unknown_dirs']] + ['']
    if b['ioc_issues']:
        L += ['### 固件命名', ''] + [f'- {mark} {x}' for x in b['ioc_issues']] + ['']
    for fi in b['firmware_internal']:
        L += [f'### 固件内部：`{fi["component"]}`', '']
        def some(xs, n=10):
            return '、'.join(f'`{x}`' for x in xs[:n]) + (f' 等 {len(xs)} 项' if len(xs) > n else '')
        if fi['core_user_files']:
            L.append(f'- {mark} Core/ 下的自写文件（无 USER CODE 标记、无 ST 版权头）→ User/App 或 User/BSP：'
                     + some(fi['core_user_files']))
        L += [f'- {mark} `{x["path"]}`：{x["hint"]}' for x in fi['extras']]
        if fi['user_unlayered']:
            L.append(f'- {mark} User/ 未按 App/BSP/ThirdParty 分层：' + some(fi['user_unlayered']))
        L.append('')
    rd = b['readme']
    if not rd['has_table']:
        L += [f'- {mark} 根 README 缺少「## 目录结构」表（第一列用反引号写路径）', '']
    elif rd['missing'] or rd['stale']:
        L += ['### README 目录表', '']
        if rd['missing']:
            L.append(f'- {mark} 未说明：' + '、'.join(f'`{x}/`' for x in rd['missing']))
        if rd['stale']:
            L.append(f'- {mark} 已不存在：' + '、'.join(f'`{x}`' for x in rd['stale']))
        L.append('')
    if not b['issues'] and not docs['root_docs'] and not docs['docs_unsorted']:
        L += ['✅ 布局合规', '']
    return L


def render_docs(r: dict) -> str:
    a, b, v, c = r['phase_a'], r['phase_b'], r['phase_c'], r['counts']
    mark = '❗' if b['declared'] else '💡'
    head = ('已声明 type = docs、layout = 1，以下为待处理项' if b['declared']
            else '未声明 VERSION（type = docs、layout = 1），布局项为整理建议，不计入待处理')
    L = [f'# one-project 文档整理扫描：{r["name"]}', '', f'- 路径：`{r["root"]}`',
         '- 类型：文档/素材型工程（不用 git，移出的文件一律进回收站）', f'- {head}',
         f'- 耗时：{r["elapsed"]} 秒；清理项 **{c["a"]}**，布局项 **{c["b"]}**'
         + (f'（另有建议 {c["b_advice"]} 项）' if c['b_advice'] else '') + f'，规范项 **{c["c"]}**', '']

    def some(xs, n=12):
        return '、'.join(f'`{x}`' for x in xs[:n]) + (f' 等 {len(xs)} 项' if len(xs) > n else '')

    L += ['## 清理', '']
    if a['candidates']:
        rows = [[f'{x["category"]} {CATEGORY_NAMES[x["category"]]}', f'`{x["path"]}`', human(x['size']), x['files'],
                 x['mtime'], x['confidence']] for x in sorted(a['candidates'], key=lambda x: (x['category'], x['path']))]
        L += [md_table(['类别', '路径', '体积', '文件数', '最近修改', '把握'], rows), '']
    if a['doc_groups']:
        L += ['### 同主题多份文档（只留最终版，需判断）', '']
        L += [f'- **{gr["topic"]}**（`{gr["dir"]}`）：' + '；'.join(f'`{posixpath.basename(f["path"])}`'
                                                                 for f in gr['files']) for gr in a['doc_groups']]
        L.append('')
    if a['nested_repos']:
        L += ['- ❗ 内含 git 仓库（代码工程应独立存放）：' + some([x['path'] for x in a['nested_repos']]), '']
    if not c['a']:
        L += ['✅ 无清理项', '']

    L += ['## 目录布局', '']
    if b['root_violations']:
        L += ['### 根目录散放', ''] + [f'- {mark} `{x["path"]}`：{x["hint"]}' for x in b['root_violations']] + ['']
    if b['unknown_dirs']:
        L += ['### 词表外顶层目录（词表：docs/ assets/ tools/ release/）', '']
        L += [f'- {mark} `{x["path"]}`：{x["hint"]}' for x in b['unknown_dirs']] + ['']
    if b['dated_dirs']:
        L += [f'- {mark} 带日期的目录（目录名不带日期）：' + some(b['dated_dirs']), '']
    d = b['docs']
    if d['unsorted'] or d['dated_names'] or d['prefixed'] or d['report_shape']:
        L += ['### docs/ 十类', '']
        if d['unsorted']:
            L.append(f'- {mark} 未归入十类：' + some(d['unsorted']))
        if d['dated_names']:
            L.append(f'- {mark} 文件名带日期或版本号（报告类与规范本体除外，版本写在文档头）：' + some(d['dated_names']))
        if d['prefixed']:
            L.append(f'- {mark} 文件名带工程名前缀：' + some(d['prefixed']))
        if d['report_shape']:
            L.append(f'- {mark} 报告应放 docs/报告/<系列>/<期标识>.<扩展名>：' + some(d['report_shape']))
        L.append('')
    L += [f'- 💡 疑似应归入新类（只提示）：`{h["path"]}` → `{h["to"]}`' for h in d['reclass_hints'][:10]]
    s = b['assets']
    if s['loose'] or s['bad_stage']:
        L += ['### assets/ 分层（参考/ 原图/ 定稿/，定稿/ 下按用途分子目录）', '']
        if s['bad_stage']:
            L.append(f'- {mark} 不在三个阶段里：' + some(s['bad_stage']))
        if s['loose']:
            L.append(f'- {mark} 散放文件：' + some(s['loose']))
        L.append('')
    if b['checksums']:
        L += [f'- {mark} 校验和与清单（进回收站）：' + some(b['checksums']), '']
    if b['sub_readmes']:
        L += [f'- {mark} 子目录 README（说明并入根 README）：' + some(b['sub_readmes']), '']
    if b['duplicates']:
        pairs: dict[tuple, int] = {}
        for g in b['duplicates']:
            key = tuple(sorted({posixpath.dirname(f) for f in g['files']}))
            pairs[key] = pairs.get(key, 0) + 1
        L += [f'### 内容完全相同的文件（{len(b["duplicates"])} 组，只留规范位置一份）', '']
        L += [f'- {mark} ' + ' ↔ '.join(f'`{k or "."}/`' for k in key) + f'：{n} 组'
              for key, n in sorted(pairs.items(), key=lambda x: -x[1])[:15]]
        L.append('')
    ln = b['links']
    if ln['broken']:
        L += ['### 失效链接', ''] + [f'- {mark} `{x["file"]}:{x["line"]}` → `{x["target"]}`' for x in ln['broken'][:30]]
        L.append('')
    if ln['review']:
        L += ['### 反引号里的失效路径（复核，不计入；版本历史行与 docs/记录、docs/报告 不查）', '']
        L += [f'- 🔍 `{x["file"]}:{x["line"]}` → `{x["path"]}`' for x in ln['review'][:30]]
        L.append('')
    rd = b['readme']
    if not rd['has_table']:
        L.append(f'- {mark} 根 README 缺少「## 目录结构」表（第一列用反引号写路径）')
    if rd['missing']:
        L.append(f'- {mark} README 目录表未说明：' + some([x + '/' for x in rd['missing']]))
    if rd['stale']:
        L.append(f'- {mark} README 目录表里已不存在：' + some(rd['stale']))
    if rd['sections_missing']:
        L.append(f'- {mark} README 缺少小节：' + '、'.join(f'「## {x}」' for x in rd['sections_missing']))
    L.append('')
    if not b['issues']:
        L += ['✅ 布局合规', '']

    L += ['## VERSION 与 release', '']
    L += [f'- ❗ {x}' for x in v['version']['issues']] or ['- ✅ VERSION 一致']
    L += [f'- ❗ {x}' for x in v['release']]
    if r['errors']:
        L += ['', '## 读取错误', ''] + [f'- {e}' for e in r['errors'][:20]]
    return '\n'.join(L)


def render_quick(r: dict) -> str:
    if not r['issues']:
        return f'one-project 轻检：✅ {r["name"]} 无问题（{r["elapsed"]} 秒）'
    return '\n'.join([f'one-project 轻检：⚠️ {r["name"]} 发现 {len(r["issues"])} 项（{r["elapsed"]} 秒）']
                     + [f'- {x}' for x in r['issues'][:20]])


def render_workspace(w: dict) -> str:
    L = ['# one-project 工作区总览', '', f'- 根目录：' + '、'.join(f'`{x}`' for x in w['roots']),
         f'- 工程数：{len(w["projects"])}；耗时 {w["elapsed"]} 秒', '']
    rows = []
    for p in w['projects']:
        g, c = p['git'], p['counts']
        bad_br = len(p['phase_c']['branches'])
        lay = f'v{LAYOUT_VERSION}' + (f'（{c["b"]} 项）' if c['b'] else '') if p['phase_b']['declared'] \
            else f'未声明（{c["b_advice"]} 项建议）'
        is_docs = p.get('type') == 'docs'
        rows.append([os.path.basename(p['workspace_root']), p['name'], '文档型' if is_docs else 'git' if g else '**否**',
                     (g or {}).get('branch', '') or '-', c['a'], human(c['a_size']), lay, c['c'],
                     '有' if p['phase_c']['version']['exists'] else '无', bad_br,
                     '、'.join(p['toolchains']) or '-'])
    rows.sort(key=lambda x: -x[4])
    L += ['## 各工程', '', md_table(['根', '工程', 'git', '当前分支', '清理项', '过程体积', '布局', '规范项', 'VERSION',
                                    '不合规分支', '工具链'], rows, 200), '']
    if w['clutter']:
        rows = [[f'`{x["path"]}`', x['note'], human(x['size'])] for x in w['clutter']]
        L += ['## 工作区级杂物', '', md_table(['路径', '说明', '体积'], rows), '']
    if w['copies']:
        rows = [[f'`{x["a"]}`', f'`{x["b"]}`', x['structure'], x['content'], x['strength']] for x in w['copies']]
        L += ['## 跨工程疑似副本（需人工确认）', '', md_table(['工程 A', '工程 B', '结构重合', '内容重合', '强度'], rows), '']
    if w['spec_groups']:
        rows = [[s['topic'], s['version'], '一致' if s['same_content'] else '**不一致**',
                 '；'.join(f'`{c}`' for c in s['copies'])] for s in w['spec_groups']]
        L += ['## 跨工程同版本规范文档', '', md_table(['主题', '版本', '内容', '分布'], rows), '']
    return '\n'.join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description='one-project 只读扫描器')
    ap.add_argument('paths', nargs='+', help='工程路径；--workspace 时为根目录列表')
    ap.add_argument('--workspace', action='store_true', help='工作区总览模式')
    ap.add_argument('--quick', action='store_true', help='任务收尾轻检')
    ap.add_argument('--docs', action='store_true', help='按文档/素材型工程规则扫描（VERSION 已声明 type = docs 时自动）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()
    try:
        if args.workspace:
            res = scan_workspace(args.paths)
            text = render_workspace(res)
            dirty = any(p['counts']['a'] or p['counts']['b'] or p['counts']['c'] for p in res['projects']) \
                or res['clutter']
        else:
            if not os.path.isdir(args.paths[0]):
                print(f'不是目录：{args.paths[0]}', file=sys.stderr)
                return 2
            res = scan_project(args.paths[0], quick=args.quick, docs=args.docs)
            if args.quick:
                text, dirty = render_quick(res), bool(res['issues'])
            else:
                cn = res['counts']
                text, dirty = render_project(res), bool(cn['a'] or cn['b'] or cn['c'])
    except Exception as e:  # noqa: BLE001 — 扫描器不能因单点异常静默
        print(f'扫描出错：{type(e).__name__}: {e}', file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1, default=list) if args.json else text)
    sys.stdout.write('\n')
    return 1 if dirty else 0


if __name__ == '__main__':
    sys.exit(main())
