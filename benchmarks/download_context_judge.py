"""Fetch one pinned GGUF weight file; never download executable model code."""
from hashlib import sha256
import json
from pathlib import Path
import requests

REV='4edb920b6f14e3b9284d4502a6485103d72cde05'
NAME='Qwen3-4B-Instruct-2507-Q8_0.gguf'
SHA='575aa0743bc47b42f49908e9cf95f59094ff8f738db5faea252979a42ad30ee9'
URL=f'https://huggingface.co/lmstudio-community/Qwen3-4B-Instruct-2507-GGUF/resolve/{REV}/{NAME}'


def main():
    path=Path('benchmarks/model_cache')/NAME
    if not path.exists():
        part=path.with_suffix('.gguf.part')
        for attempt in range(5):
            try:
                offset=part.stat().st_size if part.exists() else 0
                with requests.get(URL,headers={'Range':f'bytes={offset}-'},stream=True,timeout=45) as response:
                    response.raise_for_status()
                    with part.open('ab' if response.status_code==206 else 'wb') as out:
                        for block in response.iter_content(4*1024*1024):out.write(block)
                if part.stat().st_size!=4280404928:raise ValueError('Incomplete weight file')
                part.replace(path);break
            except requests.RequestException:
                if attempt==4:raise
    h=sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    if h.hexdigest()!=SHA:raise ValueError('Weight checksum mismatch')
    path.with_suffix('.manifest.json').write_text(json.dumps(dict(repository='lmstudio-community/Qwen3-4B-Instruct-2507-GGUF',revision=REV,filename=NAME,sha256=SHA,upstream='Qwen/Qwen3-4B-Instruct-2507'),indent=2))
    print('Verified pinned model weights',flush=True)


if __name__=='__main__':main()
