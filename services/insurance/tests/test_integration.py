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
    result = call(client, 'orders')
    assert result['code'] == 200
    assert result['json']['data']['orders'] == [
      {
        "created": "Tue, 10 Aug 2021 00:00:00 GMT", 
        "customer_email": "cposkitt@smu.edu.sg", 
        "order_id": 5, 
        "order_items": [
          {
            "game_id": 1, 
            "item_id": 9, 
            "quantity": 2
          }, 
          {
            "game_id": 2, 
            "item_id": 10, 
            "quantity": 1
          }
        ], 
        "status": "NEW"
      }, 
      {
        "created": "Tue, 10 Aug 2021 00:00:00 GMT", 
        "customer_email": "phris@coskitt.com", 
        "order_id": 6, 
        "order_items": [
          {
            "game_id": 9, 
            "item_id": 11, 
            "quantity": 1
          }
        ], 
        "status": "NEW"
      }
    ]


# This is not a dependency per se (the tests can be
# executed in any order). But if 'test_get_all' does
# not pass, there's no point in running any other
# test, so may as well skip them.

@pytest.mark.dependency(depends=['test_get_all'])
def test_one_valid(client):
    result = call(client, 'orders/6')
    assert result['code'] == 200
    assert result['json']['data'] == {
    "created": "Tue, 10 Aug 2021 00:00:00 GMT", 
    "customer_email": "phris@coskitt.com", 
    "order_id": 6, 
    "order_items": [
      {
        "game_id": 9, 
        "item_id": 11, 
        "quantity": 1
      }
    ], 
    "status": "NEW"
  }


@pytest.mark.dependency(depends=['test_get_all'])
def test_one_invalid(client):
    result = call(client, 'orders/2')
    assert result['code'] == 404
    assert result['json'] == {
        "message": "Order not found."
    }


@pytest.mark.dependency(depends=['test_get_all'])
def test_update_existing_order(client):
    result = call(client, 'orders/6', 'PATCH', {
        "status": "CANCELLED"
    })
    assert result['code'] == 200
    assert result['json']['data']['customer_email'] == "phris@coskitt.com"
    assert result['json']['data']['order_id'] == 6
    assert result['json']['data']['status'] == 'CANCELLED'
    assert result['json']['data']['order_items'] == [
      {
        "game_id": 9, 
        "item_id": 11, 
        "quantity": 1
      }
    ]


@pytest.mark.dependency(depends=['test_get_all'])
def test_create_no_body(client):
    result = call(client, 'orders', 'POST', {})
    assert result['code'] == 500


@pytest.mark.dependency(depends=['test_get_all', 'test_create_no_body'])
def test_create_one_order(client):
    result = call(client, 'orders', 'POST', {
        "customer_email": "haniel@danley.com",
        "cart_items": [
            {
                "game_id": 55,
                "quantity": 88
            }
        ]
    })
    assert result['code'] == 201
    assert result['json']['data']['customer_email'] == "haniel@danley.com"
    assert result['json']['data']['order_id'] == 7
    assert result['json']['data']['status'] == 'NEW'
    assert result['json']['data']['order_items'] == [
            {
                "item_id": 12,
                "game_id": 55,
                "quantity": 88
            }
        ]


@pytest.mark.dependency(depends=['test_create_one_order'])
def test_update_new_order(client):
    call(client, 'orders', 'POST', {
        "customer_email": "haniel@danley.com",
        "cart_items": [
            {
                "game_id": 55,
                "quantity": 88
            }
        ]
    })
    result = call(client, 'orders/7', 'PATCH', {
        "status": "CANCELLED"
    })
    assert result['code'] == 200
    assert result['json']['data']['customer_email'] == "haniel@danley.com"
    assert result['json']['data']['order_id'] == 7
    assert result['json']['data']['status'] == 'CANCELLED'
    assert result['json']['data']['order_items'] == [
            {
                "item_id": 12,
                "game_id": 55,
                "quantity": 88
            }
        ]

