def test_login(client):
    response = client.post('/login', data={
        'username': 'test',
        'password': 'demo'
    })
    assert response.status_code == 302

def test_protected_route_redirect(client):
    response = client.get('/')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']

def test_log_attendance(auth_client, mocker):
    mocker.patch('app.main.load_data', return_value=[])
    response = auth_client.post('/log', json={
        'date': '2024-01-08',
        'time': '08:30',
        'name': 'Test User',
        'status': 'in-office'
    })
    assert response.status_code == 200
