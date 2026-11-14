"""Multimedia evidence extraction with optional local/system backends.

The adapter consumes an existing local file or URL metadata. Network downloads
are deliberately outside this module; callers must provide the media artifact.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


class MediaProcessingError(RuntimeError):
    pass


def extract_subtitles(path):
    path = Path(path)
    if path.suffix.lower() in ('.srt', '.vtt', '.ass', '.txt'):
        return path.read_text(encoding='utf-8', errors='replace')
    sidecar = path.with_suffix('.srt')
    return sidecar.read_text(encoding='utf-8', errors='replace') if sidecar.exists() else ''


def extract_keyframes(path, output_dir, interval=10):
    ffmpeg = os.getenv('FFMPEG_BIN', 'ffmpeg')
    if not shutil.which(ffmpeg) and not Path(ffmpeg).exists():
        raise MediaProcessingError('未找到 ffmpeg，请安装后设置 FFMPEG_BIN')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / 'frame-%05d.jpg')
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', str(path), '-vf', f'fps=1/{max(1, int(interval))}', '-q:v', '2', pattern]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise MediaProcessingError(result.stderr[-1000:] or '关键帧提取失败')
    return [str(x) for x in sorted(output_dir.glob('frame-*.jpg'))]


def transcribe_audio(path, client=None):
    """Use an injected client or OpenAI-compatible audio endpoint."""
    if client is not None:
        with Path(path).open('rb') as audio:
            result = client.audio.transcriptions.create(model=os.getenv('ASR_MODEL', 'whisper-1'), file=audio)
        return getattr(result, 'text', str(result))
    if not os.getenv('OPENAI_API_KEY'):
        raise MediaProcessingError('ASR 未配置 OPENAI_API_KEY')
    try:
        from openai import OpenAI
        with Path(path).open('rb') as audio:
            result = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), base_url=os.getenv('OPENAI_BASE_URL') or None).audio.transcriptions.create(model=os.getenv('ASR_MODEL', 'whisper-1'), file=audio)
        return result.text
    except Exception as exc:
        raise MediaProcessingError(f'ASR 调用失败: {exc}') from exc


def ocr_image(path, client=None):
    """OCR is provider-neutral: a callable client may return text or a result."""
    if client is None:
        raise MediaProcessingError('OCR 未配置，请注入 OCR client 或配置 OCR 服务')
    result = client(path) if callable(client) else client.ocr(path)
    return getattr(result, 'text', result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))


def link_evidence(media, source_id, report_id=''):
    text = ' '.join(str(media.get(k, '')) for k in ('title', 'transcript', 'subtitles', 'ocr_text'))
    return {'source_id': source_id, 'report_id': report_id, 'evidence_type': 'multimedia', 'text': text[:10000], 'media_url': media.get('url', ''), 'artifacts': media.get('artifacts', [])}


def run(media, source_id, report_id='', output_dir=None, ocr_client=None, asr_client=None):
    item = dict(media)
    path = item.get('path')
    if path:
        p = Path(path)
        if p.exists():
            item['subtitles'] = extract_subtitles(p)
            if output_dir and p.suffix.lower() in ('.mp4', '.mov', '.mkv', '.webm', '.avi'):
                try:
                    item['artifacts'] = extract_keyframes(p, Path(output_dir) / 'frames')
                except MediaProcessingError as exc:
                    item['media_error'] = str(exc)
            if p.suffix.lower() in ('.wav', '.mp3', '.m4a', '.mp4'):
                try:
                    item['transcript'] = transcribe_audio(p, asr_client)
                except MediaProcessingError as exc:
                    item['asr_error'] = str(exc)
            if p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp') and ocr_client:
                item['ocr_text'] = ocr_image(p, ocr_client)
    item['evidence'] = link_evidence(item, source_id, report_id)
    return item
