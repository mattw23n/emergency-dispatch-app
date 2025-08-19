import pytest


@pytest.fixture
def client():
    from src import app

    app.app.config['TESTING'] = True
    
    with app.app.app_context():

        with app.db.engine.begin() as connection:
            from sqlalchemy import text
            connection.execute(text('DROP TABLE IF EXISTS `order`;'))

            connection.execute(text('''CREATE TABLE `order` (
                `order_id` int(11) NOT NULL AUTO_INCREMENT,
                `customer_email` varchar(64) NOT NULL,
                `status` varchar(10) NOT NULL,
                `created` datetime NOT NULL,
                PRIMARY KEY (`order_id`)
                ) ENGINE=InnoDB AUTO_INCREMENT=7 DEFAULT CHARSET=utf8;'''))

            connection.execute(text('DROP TABLE IF EXISTS `order_item`;'))

            connection.execute(text('''CREATE TABLE `order_item` (
                `item_id` int(11) NOT NULL AUTO_INCREMENT,
                `order_id` int(11) NOT NULL,
                `game_id` int(11) NOT NULL,
                `quantity` int(11) NOT NULL,
                PRIMARY KEY (`item_id`)
                ) ENGINE=InnoDB AUTO_INCREMENT=12 DEFAULT CHARSET=utf8;'''))

            connection.execute(text('''INSERT INTO `order` (`order_id`, `customer_email`, `status`, `created`)
                VALUES (5, 'cposkitt@smu.edu.sg', 'NEW', '2021-08-10'), (6, 'phris@coskitt.com', 'NEW', '2021-08-10');'''))

            connection.execute(text('''INSERT INTO `order_item` (`item_id`, `order_id`, `game_id`, `quantity`)
                VALUES (9, 5, 1, 2), (10, 5, 2, 1), (11, 6, 9, 1);'''))

    return app.app.test_client()
