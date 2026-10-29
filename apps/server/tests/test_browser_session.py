import pytest
from fastapi.testclient import TestClient

from life_os.api import Settings, create_app
from life_os.auth import Credentials
from life_os.store import Store


@pytest.fixture
def browser(tmp_path):
    store = Store(tmp_path / 'browser.sqlite3')
    access = Credentials(store)
    grant = access.exchange(access.issue_pairing()['pairing_code'], 'Browser')
    app = create_app(Settings(database=store.path, mode='paired', web_directory=tmp_path / 'unused'))
    with TestClient(app, base_url='https://lifeos.test') as client:
        yield client, access, grant


def login(client, token, origin='https://lifeos.test'):
    return client.post('/api/v1/browser/session', json={'token': token}, headers={'Origin': origin})


def test_browser_session_survives_requests_and_logout_removes_access(browser):
    client, _, grant = browser
    result = login(client, grant['access_token'])
    assert result.status_code == 204 and result.content == b''
    cookie = result.headers['set-cookie']
    for attribute in ('__Host-lifeos=', 'HttpOnly', 'Secure', 'SameSite=strict', 'Path=/', 'Max-Age=2592000'):
        assert attribute in cookie
    assert result.headers['cache-control'] == 'no-store'
    assert client.get('/api/v1/sync/pull').status_code == 200
    assert client.delete('/api/v1/browser/session', headers={'Origin': 'https://lifeos.test'}).status_code == 204
    assert client.get('/api/v1/sync/pull').status_code == 401


def test_browser_login_rejects_invalid_credentials_and_cross_origin(browser):
    client, _, grant = browser
    assert login(client, 'invalid').status_code == 401
    assert not client.cookies
    for origin in ('https://other.test', 'null', 'https://lifeos.test.evil'):
        assert login(client, grant['access_token'], origin).status_code == 403
    assert client.post('/api/v1/browser/session', json={'token': grant['access_token']}).status_code == 403
    assert not client.cookies


def test_cookie_writes_need_same_origin_and_revocation_still_applies(browser):
    client, access, grant = browser
    assert login(client, grant['access_token']).status_code == 204
    for headers in ({}, {'Origin': 'https://other.test'}):
        assert client.post('/api/v1/sync/push', json={}, headers=headers).status_code == 403
        assert client.delete('/api/v1/browser/session', headers=headers).status_code == 403
    # Correct-origin requests reach contract validation instead of being denied by CSRF protection.
    assert client.post('/api/v1/sync/push', json={}, headers={'Origin': 'https://lifeos.test'}).status_code == 422
    access.revoke(grant['device_id'])
    assert client.get('/api/v1/sync/pull').status_code == 401


def test_native_bearer_clients_still_work_without_origin(browser):
    client, _, grant = browser
    headers = {'Authorization': 'Bearer ' + grant['access_token']}
    assert client.get('/api/v1/sync/pull', headers=headers).status_code == 200
