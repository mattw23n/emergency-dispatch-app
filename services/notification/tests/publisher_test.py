import pika

hostname = 'localhost'
port = 5672

parameters = pika.ConnectionParameters(host=hostname,
                                       port=port
                                       )

connection = pika.BlockingConnection(parameters)

channel = connection.channel()
exchangename = "amqp.topic"
exchangetype = "topic"
channel.exchange_declare(exchange=exchangename,
                         exchange_type=exchangetype, durable=True)

channel.basic_publish(
    exchange=exchangename, routing_key="billings.notify.complete",
    body='''{ "favourite_superhero": "Superman 2" }''',
    properties=pika.BasicProperties(delivery_mode=2))

connection.close()