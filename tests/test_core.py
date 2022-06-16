import os
# import shutil
import pytest
import tablemap as tm
import sys
import pandas as pd

# pytest should be run in the root directory

@pytest.fixture(autouse=True)
def run_around_tests():
    files = ['sample.db',
             'sample.gv',
             'sample.gv.pdf']

    def remfiles():
        tempdir = os.path.join(os.getcwd(), tm.config._TEMP)
        if os.path.isdir(tempdir):
            os.rmdir(tempdir)

        for fname in files:
            fname = os.path.join(os.getcwd(), fname)
            if os.path.isfile(fname):
                os.remove(fname)

    remfiles()
    yield
    remfiles()


def test_dbfile():
    conn = tm.Conn('sample.db')
    # workspace is set to be the 'current working directory'
    assert conn.db == os.path.join(os.getcwd(), 'sample.db')

    conn1 = tm.Conn('foo/sample.db')
    assert conn1.db == os.path.join(os.getcwd(), 'foo/sample.db')
    # you can of course change directory using os.chdir


def test_loading_ordinary_csv():
    conn = tm.Conn('sample.db')
    # conn['orders'] = f('read', 'tests/Orders.csv')
    conn['orders'] = tm.read('tests/Orders.csv')
    conn.run()
    orders1 = list(conn.get('orders'))
    orders2 = tm.util.readxl('tests/Orders.csv')
    header = next(orders2)
    assert list(orders1[0].keys()) == header

    for a, b in zip(orders1, orders2):
        assert list(a.values()) == b


def test_loading_iterator():
    conn = tm.Conn('sample.db')

    def gen_orders():
        seq = tm.util.readxl("tests/Orders.csv")
        header = next(seq)
        for line in seq:
            row = dict(zip(header, line))
            yield row

    conn['orders'] = tm.read(gen_orders()) 
    conn['orders1'] = tm.read("tests/Orders.csv")

    conn.run()
    for a, b in zip(conn.get('orders'), conn.get('orders1')):
        assert a == b
 

def test_apply_order_year():
    def year(r):
        r['order_year'] = r['order_date'][:4]
        return r

    conn = tm.Conn('sample.db')
    conn['orders'] = tm.read('tests/Orders.csv')
    conn['orders1'] = tm.map(year, 'orders')
    conn.run()

    for r in conn.get('orders1'):
        assert r['order_year'] == int(r['order_date'][:4])


def test_apply_group1():
    def size(rs):
        r0 = rs[0]
        r0['n'] = len(rs)
        return r0

    conn = tm.Conn('sample.db')
    conn['order_items'] = tm.read('tests/OrderItems.csv')
    conn['order_items1'] = tm.map(size, 'order_items', by='prod_id')
    conn['order_items2'] = tm.map(size, 'order_items', by='prod_id, order_item')

    conn.run()
    assert len(list(conn.get('order_items1'))) == 7
    assert len(list(conn.get('order_items2'))) == 16


def test_join():
    conn = tm.Conn('sample.db')
    conn['products'] = tm.read('tests/Products.csv')
    conn['vendors'] = tm.read('tests/Vendors.csv')
    conn['products1'] = tm.join( 
        ['products', '*', 'vend_id'],
        ['vendors', 'vend_name, vend_country', 'vend_id']
    )

    conn.run()
    products1 = list(conn.get('products1'))
    assert products1[0]['vend_country'] == 'USA'
    assert products1[-1]['vend_country'] == 'England'

def test_parallel1():
    def revenue(r):
        r['rev'] = r['quantity'] * r['item_price']
        return r

    conn = tm.Conn('sample.db')
    conn['items'] = tm.read('tests/OrderItems.csv')
    conn['items1'] = tm.map(revenue, 'items')
    conn['items2'] = tm.map(revenue, 'items', parallel=True)

    conn.run()

    assert list(conn.get('items1')) == list(conn.get('items2'))


def test_parallel2():
    def size(rs):
        r0 = rs[0]
        r0['n'] = len(rs)
        return r0

    conn = tm.Conn('sample.db')
    conn['items'] = tm.read('tests/OrderItems.csv')
    conn['items1'] = tm.map(size, 'items', by='prod_id')
    conn['items2'] = tm.map(size, 'items', by='prod_id', parallel=True)

    conn.run()
    assert list(conn.get('items1')) == list(conn.get('items2'))


def test_full_join_using_mzip():
    def combine(xs, ys):
        if xs:
            for x in xs:
                x['prod_name'] = ys[0]['prod_name']
                yield x
        else:
            yield {
                'order_num': '',
                'order_item': '',
                'prod_id': ys[0]['prod_id'],
                'quantity': '',
                'item_price': '',
                'prod_name': ys[0]['prod_name']
                }

    conn = tm.Conn('sample.db')
    conn['items'] = tm.read('tests/OrderItems.csv')
    conn['prods'] = tm.read('tests/Products.csv')
    conn['items1'] = tm.mzip(combine, 
        [('items', 'prod_id'),
         ('prods', 'prod_id')])
    conn.run()

    items1 = pd.DataFrame(conn.get('items1'))
    assert len(items1) == 20 
