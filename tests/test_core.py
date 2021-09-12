import os
import shutil
import pytest
import tablemap as tm 

# much more work to do

@pytest.fixture(autouse=True)
def run_around_tests():
    files = ['pytest.db', 
             'pytest.gv', 
             'pytest.gv.pdf']
    def remfiles():
        for fname in files:
            if os.path.isfile(fname):
                os.remove(fname)
        tm.tablemap._JOBS = {}
        shutil.rmtree(tm.tablemap._TEMP, ignore_errors=True)
    remfiles()
    yield
    remfiles()


def test_loading_ordinary_csv():
    tm.register(orders=tm.load('tests/Orders.csv'))
    tm.run()
    orders1 = tm.get('orders')
    orders2 = tm.util.readxl('tests/Orders.csv')
    header = next(orders2)
    assert list(orders1[0].keys()) == header 

    for a, b in zip(orders1, orders2):
        assert list(a.values()) == b


def test_apply_order_year():
    def year(r):
        r['order_year'] = r['order_date'][:4]
        return r

    tm.register(
        orders=tm.load('tests/Orders.csv'),
        orders1=tm.apply(year, 'orders')
    )

    tm.run()
    for r in tm.get('orders1'):
        assert r['order_year'] == int(r['order_date'][:4])


def test_apply_group1():
    def size(rs):
        r0 = rs[0]
        r0['n'] = len(rs)
        return r0

    tm.register(
        order_items=tm.load('tests/OrderItems.csv'),
        order_items1=tm.apply(size, 'order_items', by='prod_id'),
        order_items2=tm.apply(size, 'order_items', by='prod_id, order_item'),
    )
    tm.run()
    assert len(tm.get('order_items1')) == 7
    assert len(tm.get('order_items2')) == 16 


def test_join():
    tm.register(
        products = tm.load('tests/Products.csv'),
        vendors = tm.load('tests/Vendors.csv'),
        products1 = tm.join(
            ['products', '*', 'vend_id'],
            ['vendors', 'vend_name, vend_country', 'vend_id'],
        )
    )
    tm.run()
    products1 = tm.get('products1')
    assert products1[0]['vend_country'] == 'USA'
    assert products1[-1]['vend_country'] == 'England'


def test_parallel1():
    def revenue(r):
        r['rev'] = r['quantity'] * r['item_price']
        return r

    tm.register(
        items = tm.load('tests/OrderItems.csv'),
        items1 = tm.apply(revenue, 'items'),
        items2 = tm.apply(revenue, 'items', parallel=True),

    )
    tm.run()
    assert tm.get('items1') == tm.get('items2')


def test_parallel2():
    def size(rs):
        r0 = rs[0]
        r0['n'] = len(rs)
        return r0

    tm.register(
        items = tm.load('tests/OrderItems.csv'),
        items1 = tm.apply(size, 'items', by='prod_id'),
        items2 = tm.apply(size, 'items', by='prod_id', parallel=True),

    )
    tm.run()
    assert tm.get('items1') == tm.get('items2')

