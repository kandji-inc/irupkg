import json

import pytest
import requests
from kpkg.helpers.configs import Configurator
from kpkg.helpers.utils import Utilities


@pytest.fixture(autouse=True)
def block_http_requests(monkeypatch):
    def _blocked(*args, **_kwargs):
        raise RuntimeError(f"HTTP blocked -- patch explicitly. args={args}")

    monkeypatch.setattr("urllib3.connectionpool.HTTPConnectionPool.urlopen", _blocked)


@pytest.fixture(autouse=True)
def isolate_kpkg_local_dir(tmp_path, monkeypatch):
    """Redirect kpkg's parent_dir to tmp_path so logging.basicConfig() and
    os.makedirs() in _run() don't touch the developer's $HOME during tests."""
    monkeypatch.setenv("KPKG_LOCAL_DIR", str(tmp_path))


@pytest.fixture
def fake_response_factory():
    def _make(status_code=200, body=None):
        resp = requests.models.Response()
        resp.status_code = status_code
        if body is not None:
            resp._content = json.dumps(body).encode()
        return resp

    return _make


@pytest.fixture
def combined_obj():
    class _T(Configurator, Utilities):
        pass

    return _T()
