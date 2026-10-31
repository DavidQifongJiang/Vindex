"""Download pinned official local judge and runtime; verify published SHA256."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import requests
import zipfile

ROOT=Path(__file__).resolve().parent/'model_cache'
REV='bb5d59e06d9551d752d08b292a50eb208b07ab1f'
def main():
    ROOT.mkdir(exist_ok=True)
    release=requests.get('https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b11011',timeout=60).json()
    assets=[dict(name=a['name'],url=a['browser_download_url'],sha256=a['digest'].split(':')[1]) for a in release['assets'] if a['name'] in ['cudart-llama-bin-win-cuda-12.4-x64.zip','llama-b11011-bin-win-cuda-12.4-x64.zip']]
    model=requests.get(f'https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct-GGUF/revision/{REV}?blobs=true',timeout=60).json()
    for f in model['siblings']:
        if 'q4_k_m-' in f['rfilename']:
            assets.append(dict(name=f['rfilename'],url=f'https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/{REV}/{f["rfilename"]}',sha256=f['lfs']['sha256']))
    def download(a):
        path=ROOT/a['name']
        if not path.exists():
            part=path.with_suffix(path.suffix+'.part')
            for attempt in range(12):
                offset=part.stat().st_size if part.exists() else 0
                try:
                    with requests.get(a['url'],headers={'Range':f'bytes={offset}-'},stream=True,timeout=60) as r:
                        r.raise_for_status()
                        with part.open('ab' if r.status_code==206 else 'wb') as out:
                            for chunk in r.iter_content(1024*1024):out.write(chunk)
                    part.replace(path)
                    break
                except requests.RequestException as exc:
                    print('Retry',a['name'],attempt+1,type(exc).__name__,flush=True)
                    if attempt==11:raise
        h=sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=a['sha256']:raise ValueError('Hash mismatch '+a['name'])
        print('Verified',a['name'],flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(download,assets))
    for a in assets:
        if a['name'].endswith('.zip'):
            with zipfile.ZipFile(ROOT/a['name']) as z:z.extractall(ROOT/'runtime')
    (ROOT/'manifest.json').write_text(json.dumps(assets,indent=2))
if __name__=='__main__':main()
