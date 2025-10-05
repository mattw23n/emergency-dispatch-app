import json
import pytest


def call(client, path, method='GET', body=None):
    mimetype = 'application/json'
    headers = {
        'Content-Type': mimetype,
        'Accept': mimetype
    }

    if method == 'POST':
        response = client.post(path, data=json.dumps(body), headers=headers)
    elif method == 'PATCH':
        response = client.patch(path, data=json.dumps(body), headers=headers)
    elif method == 'DELETE':
        response = client.delete(path)
    else:
        response = client.get(path)

    return {
        "json": json.loads(response.data.decode('utf-8')),
        "code": response.status_code
    }


@pytest.mark.dependency()
def test_health(client):
    result = call(client, 'health')
    assert result['code'] == 200


@pytest.mark.dependency()
def test_get_all(client):
    result = call(client, 'billings')
    assert result['code'] == 200
    assert 'billings' in result['json']['data']
    assert isinstance(result['json']['data']['billings'], list)


@pytest.mark.dependency(depends=['test_get_all'])
def test_one_valid(client):
    result = call(client, 'billings/1')
    assert result['code'] == 200
    data = result['json']['data']
    assert 'billing_id' in data
    assert 'customer_email' in data
    assert 'amount' in data
    assert 'status' in data


@pytest.mark.dependency(depends=['test_get_all'])
def test_one_invalid(client):
    result = call(client, 'billings/999')
    assert result['code'] == 404
    assert result['json'] == {
        "message": "Billing not found."
    }


@pytest.mark.dependency(depends=['test_get_all'])
def test_create_no_body(client):
    result = call(client, 'billings', 'POST', {})
    assert result['code'] in (400, 500)  # depending on your API validation
    assert 'message' in result['json']


@pytest.mark.dependency(depends=['test_get_all', 'test_create_no_body'])
def test_create_one_billing(client):
    body = {
        "customer_email": "alice@example.com",
        "amount": 199.99,
        "currency": "SGD",
        "status": "PENDING",
        "order_id": 6
    }
    result = call(client, 'billings', 'POST', body)
    assert result['code'] == 201
    data = result['json']['data']
    assert data['customer_email'] == "alice@example.com"
    assert data['amount'] == 199.99
    assert data['currency'] == "SGD"
    assert data['status'] == "PENDING"


@pytest.mark.dependency(depends=['test_create_one_billing'])
def test_update_billing_status(client):
    result = call(client, 'billings/1', 'PATCH', {
        "status": "PAID"
    })
    assert result['code'] == 200
    data = result['json']['data']
    assert d
