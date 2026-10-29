#!/usr/bin/env python3
"""Архив вышедших публикаций Instagram (Reels, Stories, карусели) — решение пользователя 29.10.2026:
«новый репозиторий, куда надо складывать уже опубликованные рилс и сторис и карусели для инсты, надо подписывать
какого числа и в какой из аккаунтов вышел контент и переносить, а также перенести уже вышедшие ролики».

  python3 tools/archive.py --post-hub ../post-hub --video-hub ../VIDEO-HUB [--limit-mb 900] [--dry-run]

Берёт вышедшие посты POST-HUB (<ПРОЕКТ>/INSTAGRAM/<ФОРМАТ>/PUBLISHED/ГГГГ-ММ/<пост>/: post.md + info.json от публикатора)
и кладёт сюда:
  instagram/<аккаунт>/ГГГГ-ММ/ГГГГ-ММ-ДД_ЧЧММ_<формат>_<тема>/
      файлы поста (ролик, части Stories, слайды карусели) · пост.md (подпись и шапка из POST-HUB) · info.json
Дата и время — фактический выход по Москве (info.json публикатора). Ролики — то, что ушло в Instagram: копия из
VIDEO-HUB (finished-videos/publish/…) или мастер из Git LFS; файл больше 95 МБ пережимается (H.264 ~4 Мбит/с,
лимит GitHub — 100 МБ на файл, LFS здесь не используется). Уже перенесённое не трогает. Список всего — index.json
и ИНДЕКС.md в корне. --limit-mb — сколько нового за один заход (дальше коммит, пуш и следующий заход).
"""
import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MSK = dt.timezone(dt.timedelta(hours=3))
ACCOUNTS = {  # проект POST-HUB → аккаунт Instagram
    'GTT': ('globaltech_tour', 'Global Tech Tour'),
    'AURA': ('aura_robotics_ru', 'Aura Robotics'),
    'OSTAPDOTCENKO': ('ostap.dotsenko', 'Остап Доценко'),
}
FORMATS = {'REELS': ('reels', 'Reels'), 'STORIES': ('stories', 'Stories'), 'CAROUSEL': ('karusel', 'Карусель')}
MEDIA = ('.jpg', '.jpeg', '.png', '.mp4', '.mov', '.m4v')
VIDEO = ('.mp4', '.mov', '.m4v')
VH_RAW = 'https://raw.githubusercontent.com/Ostap-87/VIDEO-HUB/main/'
VH_LFS = 'https://media.githubusercontent.com/media/Ostap-87/VIDEO-HUB/main/'
MAX_FILE = 95 * 2**20


def head(text):
    """Шапка post.md (простые поля «ключ: значение», списки в JSON)."""
    out = {}
    if not text.startswith('---\n'):
        return out
    for line in text[4:].split('\n---\n', 1)[0].splitlines():
        if ':' not in line or line.startswith((' ', '#')):
            continue
        k, v = line.split(':', 1)
        v = v.strip()
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v.strip('"')
    return out


def is_pointer(p):
    return p.exists() and p.stat().st_size < 2048 and p.read_bytes().startswith(b'version https://git-lfs')


def fetch(url, tmp):
    dst = Path(tmp) / Path(urllib.parse.urlparse(url).path).name
    with urllib.request.urlopen(url, timeout=600) as r, open(dst, 'wb') as f:
        shutil.copyfileobj(r, f)
    return dst


def source(ref, vh, tmp):
    """Файл поста: путь VIDEO-HUB или ссылка → локальный файл (скачивает, если на диске только указатель LFS)."""
    if ref.startswith(VH_RAW):
        rel = urllib.parse.unquote(ref[len(VH_RAW):])
    elif ref.startswith(('http://', 'https://')):
        return fetch(ref, tmp)
    else:
        rel = ref
    local = vh / rel
    if local.exists() and not is_pointer(local):
        return local
    return fetch(ref if ref.startswith(VH_RAW) else VH_LFS + urllib.parse.quote(rel), tmp)


def put(src, dst):
    if src.suffix.lower() in VIDEO and src.stat().st_size > MAX_FILE:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-c:v', 'libx264', '-preset', 'medium', '-b:v', '4000k',
                        '-maxrate', '4600k', '-bufsize', '9000k', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k',
                        '-movflags', '+faststart', str(dst.with_suffix('.mp4'))], check=True)
        return dst.with_suffix('.mp4')
    shutil.copyfile(src, dst)
    return dst


def published(ph):
    for d in sorted(ph.glob('*/INSTAGRAM/*/PUBLISHED/*/*/')):
        project, _, fmt = d.parts[-6], d.parts[-5], d.parts[-4]
        if project in ACCOUNTS and fmt in FORMATS and (d / 'post.md').exists():
            yield project, fmt, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--post-hub', required=True)
    ap.add_argument('--video-hub', required=True)
    ap.add_argument('--limit-mb', type=float, default=900)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    ph, vh = Path(a.post_hub).resolve(), Path(a.video_hub).resolve()
    idx_path = ROOT / 'index.json'
    index = json.loads(idx_path.read_text()) if idx_path.exists() else []
    done = {e['source'] for e in index}
    added, total = 0, 0
    left = 0
    for project, fmt, d in published(ph):
        src_rel = str(d.relative_to(ph))
        if src_rel in done:
            continue
        if total > a.limit_mb * 2**20:
            left += 1
            continue
        meta = head((d / 'post.md').read_text())
        info = json.loads((d / 'info.json').read_text()) if (d / 'info.json').exists() else {}
        when = dt.datetime.fromisoformat(info['published_at']).astimezone(MSK) if info.get('published_at') else \
            dt.datetime.strptime(d.name[:15], '%Y-%m-%d_%H%M').replace(tzinfo=MSK)
        handle, account = ACCOUNTS[project]
        flabel, fname = FORMATS[fmt]
        topic = d.name[16:] if d.name[:4].isdigit() else d.name
        rel = Path('instagram') / handle / when.strftime('%Y-%m') / f"{when:%Y-%m-%d_%H%M}_{flabel}_{topic}"
        refs = [str(p) for p in sorted(d.iterdir()) if p.suffix.lower() in MEDIA]
        urls = meta.get('media_urls') or []
        refs += [urls] if isinstance(urls, str) else urls
        hub = meta.get('video_hub') or []
        refs += [x for x in ([hub] if isinstance(hub, str) else hub) if x.lower().endswith(VIDEO)]
        # одна и та же картинка бывает и файлом в папке поста, и ссылкой на её копию в VIDEO-HUB
        seen, uniq = set(), []
        for r in refs:
            n = Path(urllib.parse.urlparse(r).path).name
            if n not in seen:
                seen.add(n)
                uniq.append(r)
        print(f"{when:%d.%m %H:%M} @{handle} {fname}: {topic} — файлов {len(uniq)}")
        if a.dry_run:
            continue
        out = ROOT / rel
        out.mkdir(parents=True, exist_ok=True)
        files = []
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as tmp:
            for i, r in enumerate(uniq, 1):
                src = Path(r) if Path(r).is_absolute() else source(r, vh, tmp)
                name = src.name if fmt == 'CAROUSEL' or len(uniq) == 1 else f'{i:02d}_{src.name}'
                got = put(src, out / name)
                files.append(got.name)
                total += got.stat().st_size
        shutil.copyfile(d / 'post.md', out / 'пост.md')
        entry = {
            'account': f'@{handle}', 'account_name': account, 'network': 'Instagram', 'format': fname,
            'published_at': when.isoformat(timespec='seconds'), 'url': info.get('url', ''), 'post_id': info.get('post_id', ''),
            'title': meta.get('title', topic), 'trial': meta.get('trial', ''), 'files': files, 'path': str(rel),
            'source': src_rel,
        }
        (out / 'info.json').write_text(json.dumps(entry, ensure_ascii=False, indent=1) + '\n')
        index.append(entry)
        added += 1
    if a.dry_run:
        return
    index.sort(key=lambda e: e['published_at'])
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + '\n')
    write_md(index)
    print(f'перенесено {added}, {total / 2**20:.0f} МБ; осталось {left}')


def write_md(index):
    lines = ['# Индекс вышедших публикаций Instagram', '',
             'Собирается командой `python3 tools/archive.py` из `index.json`. Время — фактический выход, по Москве.', '']
    for handle, account in ACCOUNTS.values():
        rows = [e for e in index if e['account'] == f'@{handle}']
        if not rows:
            continue
        lines += [f'## {account} — @{handle}', '', f'Всего: {len(rows)}.', '',
                  '| Дата | Время МСК | Формат | Тема | Пост | Файлы |', '|---|---|---|---|---|---|']
        for e in sorted(rows, key=lambda e: e['published_at'], reverse=True):
            t = dt.datetime.fromisoformat(e['published_at'])
            fmt = e['format'] + (' (пробный)' if e.get('trial') else '')
            link = f"[открыть]({e['url']})" if e.get('url') else '—'
            title = str(e['title']).replace('|', '/')
            lines.append(f"| {t:%d.%m.%Y} | {t:%H:%M} | {fmt} | {title} | {link} | [папка]({urllib.parse.quote(e['path'])}/) |")
        lines.append('')
    (ROOT / 'ИНДЕКС.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main())
