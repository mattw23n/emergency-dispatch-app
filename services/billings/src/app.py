import os
import socket

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS

from datetime import datetime

app = Flask(__name__)

if os.environ.get('db_conn'):
    app.config['SQLALCHEMY_DATABASE_URI'] = \
            os.environ.get('db_conn') + '/order'
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = \
            'mysql+mysqlconnector://cs302:cs302@localhost:3306/order'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_size': 100,
                                           'pool_recycle': 280}

db = SQLAlchemy(app)

CORS(app)


class Order(db.Model):
    __tablename__ = 'order'

    order_id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(10), nullable=False)
    created = db.Column(db.DateTime, nullable=False, default=datetime.now)

    def to_dict(self):
        dto = {
            "order_id": self.order_id,
            "customer_email": self.customer_email,
            "status": self.status,
            "created": self.created
        }

        dto["order_items"] = []

        for order_item in self.order_items:
            dto["order_items"].append(order_item.to_dict())

        return dto


class Order_Item(db.Model):
    __tablename__ = 'order_item'

    item_id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.ForeignKey(
        'order.order_id', ondelete='CASCADE', onupdate='CASCADE'),
        nullable=False, index=True)

    game_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    order = db.relationship(
        'Order', primaryjoin='Order_Item.order_id == Order.order_id',
        backref='order_items')

    def to_dict(self):
        return {
            'item_id': self.item_id,
            'game_id': self.game_id,
            'quantity': self.quantity
        }


@app.route("/health")
def health_check():
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)

    return jsonify(
            {
                "message": "Service is healthy.",
                "service:": "orders",
                "ip_address": local_ip
            }
    ), 200


@app.route("/orders")
def get_all():
    order_list = db.session.scalars(
                   db.select(Order)
                ).all()
    if len(order_list) != 0:
        return jsonify(
            {
                "data": {
                    "orders": [order.to_dict() for order in order_list]
                }
            }
        ), 200
    return jsonify(
        {
            "message": "There are no orders."
        }
    ), 404


@app.route("/orders/<int:order_id>")
def find_by_id(order_id):
    order = db.session.scalar(
              db.select(Order).
              filter_by(order_id=order_id)
           )
    if order:
        return jsonify(
            {
                "data": order.to_dict()
            }
        ), 200
    return jsonify(
        {
            "message": "Order not found."
        }
    ), 404


@app.route("/orders", methods=['POST'])
def new_order():
    try:
        customer_email = request.json.get('customer_email')
        order = Order(customer_email=customer_email, status='NEW')

        cart_items = request.json.get('cart_items')
        for item in cart_items:
            order.order_items.append(Order_Item(
                game_id=item['game_id'], quantity=item['quantity']))

        db.session.add(order)
        db.session.commit()
    except Exception as e:
        return jsonify(
            {
                "message": "An error occurred creating the order.",
                "error": str(e)
            }
        ), 500

    return jsonify(
        {
            "data": order.to_dict()
        }
    ), 201


@app.route("/orders/<int:order_id>", methods=['PATCH'])
def update_order(order_id):
    order = db.session.scalar(
              db.select(Order).
              with_for_update(of=Order).
              filter_by(order_id=order_id)
           )
    if order is not None:
        data = request.get_json()
        if 'status' in data.keys():
            order.status = data['status']
        try:
            db.session.commit()
        except Exception as e:
            return jsonify(
                {
                    "message": "An error occurred updating the order.",
                    "error": str(e)
                }
            ), 500
        return jsonify(
            {
                "data": order.to_dict()
            }
        )
    return jsonify(
        {
            "data": {
                "order_id": order_id
            },
            "message": "Order not found."
        }
    ), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
