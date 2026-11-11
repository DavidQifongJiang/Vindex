"""Secret-cookie guarded, isolated processing experiment; no destructive HTTP controls."""
import hmac
from http.cookies import SimpleCookie
import os

from fastapi.staticfiles import StaticFiles
from guard import validate_environment


def build_app():
    validate_environment(os.environ)
    from app.main import app
    app.mount('/bench-api', StaticFiles(directory='/bench/frontend-api', html=True))
    app.mount('/bench-direct', StaticFiles(directory='/bench/frontend-direct', html=True))

    @app.post('/bench/pipeline/warm-query')
    def warm_query():
        from app.services.search_service import encode_text
        return {'query_model_warmed': len(encode_text('vector search')) == 384}

    class Guard:
        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http':
                cookies = SimpleCookie()
                cookies.load(dict(scope['headers']).get(b'cookie', b'').decode('latin-1'))
                supplied = cookies.get('vindex_bench')
                if not supplied or not hmac.compare_digest(supplied.value, os.environ['VINDEX_BENCH_TOKEN']):
                    await send({'type':'http.response.start','status':403,'headers':[]})
                    return await send({'type':'http.response.body','body':b'Experiment access required'})
            return await app(scope, receive, send)
    return Guard()


if os.getenv('VINDEX_PIPELINE_EXPERIMENT') == 'true':
    application = build_app()
