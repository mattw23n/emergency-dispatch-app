import pika
import time
from os import environ

hostname = environ.get('rabbitmq_host') or 'localhost'
port = int(environ.get('rabbitmq_port') or 5672)

print(f'Attempting to connect to RabbitMQ at {hostname}:{port}')

parameters = pika.ConnectionParameters(host=hostname, port=port)

connected = False
start_time = time.time()
max_retry_time = 60  # Increase retry time

print('Connecting to RabbitMQ...')

while not connected:
    try:
        connection = pika.BlockingConnection(parameters)
        connected = True
        print('Successfully connected to RabbitMQ!')
    except pika.exceptions.AMQPConnectionError as e:
        print(f'Connection failed: {e}')
        if time.time() - start_time > max_retry_time:
            print('Max retry time exceeded. Exiting.')
            exit(1)
        print('Retrying in 2 seconds...')
        time.sleep(2)

print('CONNECTED!')

try:
    # Declare Exchange (Always amqp.topic)
    channel = connection.channel()
    exchange_name = "amqp.topic"
    exchange_type = "topic"
    
    print(f'Creating exchange: {exchange_name}')
    channel.exchange_declare(exchange=exchange_name,
                             exchange_type=exchange_type, durable=True)
    print(f'Exchange {exchange_name} created successfully!')

    # Declare Queue
    queue_name = 'Dispatch'
    print(f'Creating queue: {queue_name}')
    channel.queue_declare(queue=queue_name, durable=True)
    print(f'Queue {queue_name} created successfully!')
    
    # Set binding for the Queue
    print(f'Binding queue {queue_name} to exchange {exchange_name}')
    channel.queue_bind(exchange=exchange_name,
                       queue=queue_name, routing_key='wearable.data')
    print('Queue binding completed successfully!')
    
    print(f'Binding queue {queue_name} to exchange {exchange_name}')
    channel.queue_bind(exchange=exchange_name,
                       queue=queue_name, routing_key='event.dispatch')
    print('Second queue binding completed successfully!') 
    
    connection.close()
    print('AMQP setup completed successfully!')
    
except Exception as e:
    print(f'Error during AMQP setup: {e}')
    if connection:
        connection.close()
    raise